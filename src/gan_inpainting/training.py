import argparse
import logging
import os

import numpy as np
import cv2

import torch
import torchvision
from torchvision import transforms as T
from torchvision.utils import save_image
import torch.nn.functional as F
import torch.nn as nn
import torch.distributed as dist
from torch.utils.data import ConcatDataset, DataLoader
from torch.nn.parallel import DistributedDataParallel as DDP
import torch.multiprocessing as mp

from apex import amp

import matplotlib.pyplot as plt

from generator import *
from discriminator import Discriminator
from loss import *
from dataset import FakeDataset, FaceMaskDataset

from metrics import TrainingMetrics

from augmentation import AugmentPipe

# torch.autograd.set_detect_anomaly(True)
# a loss history should be held to keep tracking if the network is learning
# something or is doing completely random shit
# also a logger would be nice
def train(gpu, args):
    logging.basicConfig(filename='output.log', level=logging.INFO)
    rank = args.nr * args.gpus + gpu
    dist.init_process_group(
        backend='nccl',
        init_method='env://',
        world_size=args.world_size,
        rank=rank
    )
    torch.manual_seed(0)
    torch.cuda.set_device(gpu)
    logging.info(f'[p#{rank}] joined the training on gpu#{gpu}!')

    dataset = FaceMaskDataset(
            args.dataset_dir,
            'maskffhq.csv',
            T.Resize(args.input_size)
        )

    sampler = torch.utils.data.distributed.DistributedSampler(
        dataset,
        num_replicas=args.world_size,
        rank=rank
    )

    dataloader = DataLoader(
            dataset=dataset,
            batch_size=args.batch_size,
            shuffle=False,
            pin_memory=True,
            num_workers=0,
            sampler=sampler)

    netG = MSSAGenerator(input_size=args.input_size)
    netD = Discriminator(input_size=args.input_size)

    netG.cuda(gpu)
    netD.cuda(gpu)


    optimG = torch.optim.Adam(
                netG.parameters(),
                lr=args.learning_rate_g,
                betas=(0.5, 0.999)
            )
    optimD = torch.optim.Adam(
                netD.parameters(),
                lr=args.learning_rate_d,
                betas=(0.5, 0.999)
            )

    netG, optimG = amp.initialize(netG, optimG, opt_level='O2')
    netD, optimD = amp.initialize(netD, optimD, opt_level='O2')

    netG = DDP(netG, device_ids=[gpu])
    netD = DDP(netD, device_ids=[gpu])
    # Resume checkpoint if necessary
    if args.checkpoint is True:
        generator_dir = f'{args.checkpoint_dir}/generator.pt'
        discriminator_dir = f'{args.checkpoint_dir}/discriminator.pt'
        opt_generator_dir = f'{args.checkpoint_dir}/opt_generator.pt'
        opt_discriminator_dir = f'{args.checkpoint_dir}/opt_discriminator.pt'

        if os.path.isfile(generator_dir):
            logging.info(f'[p#{rank}] resuming training of generator')
            checkpointG = torch.load(generator_dir)
            netG.load_state_dict(checkpointG)

        if os.path.isfile(discriminator_dir):
            logging.info(f'[p#{rank}] resuming training of discriminator')
            checkpointD = torch.load(discriminator_dir)
            netD.load_state_dict(checkpointD)

        if os.path.isfile(opt_generator_dir):
            logging.info(f'[p#{rank}] resuming training of opt_generator')
            checkpointOG = torch.load(opt_generator_dir)
            optimG.load_state_dict(checkpointOG)

        if os.path.isfile(opt_discriminator_dir):
            logging.info(f'[p#{rank}] resuming training of opt_discriminator')
            checkpointOD = torch.load(opt_discriminator_dir)
            optimD.load_state_dict(checkpointOD)

    lossG = GeneratorLoss()
    lossRecon = L1ReconLoss()
    lossTV = TVLoss()
    lossD = DiscriminatorHingeLoss()
    lossVGG = VGGLoss(gpu)
    lossContra = InfoNCE()

    # only rank 0 update metrics
    if(rank == 0):
        logging.info(f'[p#{rank}] preparing the metric class')
        metrics = TrainingMetrics(
                args.screenstep,
                args.video_dir,
                args.plots_dir,
                dataset
            )

    netG.train()
    netD.train()

    losses = {
            'g': [],
            'd': [],
            'r': [],
            'tv': [],
            'perc': [],
            'style': [],
            'contra': []
            }

    accuracies = {
            'd': []
            }

    for ep in range(args.epochs):
        total_ds_size = len(dataloader)
        for i, (imgs, masks) in enumerate(dataloader):
            netG.zero_grad()
            netD.zero_grad()
            optimG.zero_grad()
            optimD.zero_grad()
            lossG.zero_grad()
            lossD.zero_grad()
            lossTV.zero_grad()
            lossVGG.zero_grad()

            aug_t = AugmentPipe(
                        xflip=1.,
                        xint=0.75,
                        brightness=0.75,
                        contrast=0.75,
                        hue=1.,
                        saturation=0.75)

            # meh, too many cats/splits
            imgs_masks = torch.cat([imgs, masks], dim=1)
            aug_imgs_masks = aug_t(imgs_masks)
            aug_imgs, aug_masks = torch.split(aug_imgs_masks, [3,1], dim=1)
            imgs = torch.cat([imgs, aug_imgs], dim=0)
            masks = torch.cat([masks, aug_masks], dim=0)

            imgs = imgs.cuda(gpu, non_blocking=True)
            masks = masks.cuda(gpu, non_blocking=True)

            # change img range from [0,255] to [-1,+1]
            imgs = imgs / 127.5 - 1
            masks = masks / 1.

            # forward G
            emb_repr, coarse_out, refined_out = netG(imgs, masks)
            reconstructed_coarses = coarse_out*masks + imgs*(1-masks)
            reconstructed_imgs = refined_out*masks + imgs*(1-masks)

            pos_neg_imgs = torch.cat([imgs, reconstructed_imgs], dim=0)
            dmasks = torch.cat([masks, masks], dim=0)

            # forward D
            pred_pos_neg_imgs = netD(pos_neg_imgs, dmasks)
            pred_pos_imgs, pred_neg_imgs = torch.chunk(pred_pos_neg_imgs, 2, dim=0)

            # loss + backward D
            loss_discriminator = lossD(pred_pos_imgs, pred_neg_imgs)
            losses['d'] = loss_discriminator.item()

            with amp.scale_loss(loss_discriminator, optimD) as scaled_loss:
                scaled_loss.backward(retain_graph=True)
            optimD.step()

            netG.zero_grad()
            netD.zero_grad()
            optimG.zero_grad()
            optimD.zero_grad()
            lossG.zero_grad()
            lossD.zero_grad()

            # loss + backward G
            pred_neg_imgs = netD(reconstructed_imgs, masks)
            loss_generator = lossG(pred_neg_imgs)
            loss_recon = lossRecon(imgs, coarse_out, refined_out, dmasks)
            loss_tv = lossTV(refined_out)
            loss_perc, loss_style = lossVGG(imgs, refined_out)
            loss_perc *= 0.05
            loss_style *= 40
            loss_contra = lossContra(*emb_repr.chunk(2))
            loss_gen_recon = loss_generator + loss_recon + \
                    loss_tv + loss_perc + loss_style + loss_contra

            losses['g'] = loss_generator.item()
            losses['r'] = loss_recon.item()
            losses['tv'] = loss_tv.item()
            losses['perc'] = loss_perc.item()
            losses['style'] = loss_style.item()
            losses['contra'] = loss_contra.item()

            with amp.scale_loss(loss_gen_recon, optimG) as scaled_loss:
                scaled_loss.backward()
            optimG.step()

            # every 100 img, print losses, update the graph, output an image as
            # example
            if i % args.screenstep == 0:
                logging.info(
                        f'[p#{rank}] epoch: {ep}/{args.epochs}' + \
                        f'\tstep: {i}/{total_ds_size}' + \
                        f'\tloss: {loss_gen_recon.item()}'
                    )

            if rank == 0 and i % args.screenstep == 0:
                aug_checkpoint_coarse = ((reconstructed_coarses[-1] + 1) * 127.5)
                aug_checkpoint_recon = ((reconstructed_imgs[-1] + 1) * 127.5)

                checkpoint_coarse = ((reconstructed_coarses[0] + 1) * 127.5)
                checkpoint_recon = ((reconstructed_imgs[0] + 1) * 127.5)

                save_image(((imgs[0] + 1) * 127.5) / 255, f'{args.plots_dir}/orig_{i}.png')
                save_image(checkpoint_coarse / 255, f'{args.plots_dir}/coarse_{i}.png')
                save_image(checkpoint_recon / 255, f'{args.plots_dir}/recon_{i}.png')

                save_image(((aug_imgs[-1] + 1) * 127.5) / 255, f'{args.plots_dir}/aug_{i}.png')
                save_image(aug_checkpoint_coarse / 255, f'{args.plots_dir}/aug_coarse_{i}.png')
                save_image(aug_checkpoint_recon / 255, f'{args.plots_dir}/aug_recon_{i}.png')

                # maybe save them in metrics.update()
                torch.save(netG.state_dict(), f'{args.checkpoint_dir}/generator.pt')
                torch.save(netD.state_dict(), f'{args.checkpoint_dir}/discriminator.pt')
                torch.save(optimG.state_dict(), f'{args.checkpoint_dir}/opt_generator.pt')
                torch.save(optimD.state_dict(), f'{args.checkpoint_dir}/opt_discriminator.pt')
            if rank == 0:
                metrics.update(losses, pred_pos_neg_imgs, netG, netD)
        torch.save(netG.state_dict(), f'{args.checkpoint_dir}/generator.pt')
        torch.save(netD.state_dict(), f'{args.checkpoint_dir}/discriminator.pt')
        torch.save(optimG.state_dict(), f'{args.checkpoint_dir}/opt_generator.pt')
        torch.save(optimD.state_dict(), f'{args.checkpoint_dir}/opt_discriminator.pt')
        logging.info(f'[p#{rank}] training ended.')
    return

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Training")
    parser.add_argument("--checkpoint", default=False, help="resume training")
    parser.add_argument("--screenstep", default=100, type=int, help="how often output metrics and imgs")
    parser.add_argument("--nodes", default=1, type=int, help="number of nodes")
    parser.add_argument("--gpus", default=1, type=int, help="number of gpus per node")
    parser.add_argument("--nr", default=0, type=int, help="ranking within the nodes")
    parser.add_argument("--epochs", default=1, type=int, help="number of total epochs to run")
    parser.add_argument("--batch_size", default=2, type=int, help="batch size")
    parser.add_argument("--input_size", default=256, type=int, help="size of the imgs")
    parser.add_argument("--learning_rate_g", default=0.0001, type=float, help="learning rate of the generator")
    parser.add_argument("--learning_rate_d", default=0.0004, type=float, help="learning rate of the discriminator")
    parser.add_argument("--dataset_dir", type=str, help="dataset location", required=True)
    parser.add_argument("--checkpoint_dir", type=str, help="where to load/save checkpoints", required=True)
    parser.add_argument("--plots_dir", type=str, help="where to save the plots", required=True)
    parser.add_argument("--video_dir", type=str, help="where to save the video", required=True)
    args = parser.parse_args()

    args.world_size = args.gpus*args.nodes

    logging.basicConfig(filename='output.log', level=logging.INFO)
    logging.info(f'[p#0] nr: {args.nr}, nodes: {args.nodes}, gpus: {args.gpus}')

    logging.info(f'[p#0] starting {args.world_size} processes')
    mp.spawn(train, nprocs=args.gpus, args=(args,))
