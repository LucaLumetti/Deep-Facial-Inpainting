import torch
import matplotlib.pyplot as plt
from torchvision.utils import save_image
import numpy as np

class TrainingMetrics:

    def __init__(self, dataloader):
        self.lossG = []
        self.lossD = []
        self.lossR = []
        self.accuracy = []
        self.difference = []
        self.max_distance = None
        self.dataloader = dataloader
        self.img, self.mask = next(iter(dataloader))

    '''
        Parameters:
            lossG: loss for Generator
            lossD: loss for Discriminator
            lossR: loss for Refined
            D_result: result of Discriminator for accuracy
            netG: Generator Net for testing
            netD: Discriminator Net for testing
    '''
    def update(self, lossG, lossD, lossR, D_result, netG, netD):
        self.lossG.append(lossG)
        self.lossD.append(lossD)
        self.lossR.append(lossR)

        pred_pos_imgs, pred_neg_imgs = torch.chunk(D_result, 2, dim=0)

        # with torch.inference_mode():
        mean_pos_pred = pred_pos_imgs.mean(dim=1)
        mean_neg_pred = pred_neg_imgs.mean(dim=1)
        mean_pos_pred[mean_pos_pred > 0.5] = 1
        mean_pos_pred[mean_pos_pred <= 0.5] = 0

        mean_neg_pred[mean_neg_pred > 0.5] = 1
        mean_neg_pred[mean_neg_pred <= 0.5] = 0
        mean_neg_pred = torch.Tensor([1 if elem == 0 else 0 for elem in mean_neg_pred])

        accuracyD = torch.sum(mean_pos_pred) + torch.sum(mean_neg_pred)
        accuracyD /= mean_pos_pred.shape[0] + mean_neg_pred.shape[0]
        self.accuracy.append(accuracyD)

        # every 100 img, print losses, update the graph, output an image as example
        if len(self.lossG) % 100 == 0:
            print(f"[{i}]\t" + \
                  f"loss_g: {self.lossG[-1]}, " + \
                  f"loss_d: {self.lossD[-1]}, " + \
                  f"loss_r: {self.lossR[-1]}, " + \
                  f"accuracy_d: {accuracy[-1]}")

            fig, axs = plt.subplots(4, 1)
            x_axis = range(len(self.lossG))
            # loss g
            axs[0].plot(x_axis, self.lossG, x_axis, self.lossR)
            axs[0].set_xlabel('iterations')
            axs[0].set_ylabel('loss')
            # loss d
            axs[1].plot(x_axis, self.lossD)
            axs[1].set_xlabel('iterations')
            axs[1].set_ylabel('loss')
            # acc d
            axs[2].plot(x_axis, accuracies['d'])
            axs[2].set_xlabel('iterations')
            axs[2].set_ylabel('accuracy')
            axs[2].set_ylim(0, 1)


            #using CPU
            #img = self.img.to(device)
            #mask = self.mask.to(device)

            # change img range from [0,255] to [-1,+1]
            img = self.img / 127.5 - 1

            coarse_out, refined_out = netG(img, self.mask)
            reconstructed_imgs = refined_out * self.mask + imgs * (1 - self.mask)
            checkpoint_recon = ((reconstructed_imgs[0] + 1) * 127.5)
            checkpoint_img = ((img[0] + 1) * 127.5)
            #calculate MSE
            self.difference.append(self._mse(checkpoint_img,checkpoint_recon))
            #plot MSE
            i = range(len(self.difference))
            axs[3].plot(i, self.difference)
            axs[3].set_xlabel('checkpoint')
            axs[3].set_ylabel('MSE')

            fig.tight_layout()
            fig.savefig('plots/loss.png', dpi=fig.dpi)
            plt.close(fig)

            save_image(checkpoint_recon / 255, 'plots/recon.png')
            save_image(checkpoint_img / 255, 'plots/orig.png')

    def _mse(self, img1, img2):
        err = np.sum((img1.astype(float)-img2.astype(float))**2)
        err /= float(img1.shape[0]*img1.shape[1])
        return err
