import numpy as np
import pandas as pd
import os
import cv2

import torch
import torch.nn.functional as F
from torchvision import transforms as T
import torch.nn as nn
from torchvision.io import read_image
from torchvision.utils import save_image

from torch.utils.data import Dataset, DataLoader

from augmentation import AugmentPipe

class FakeDataset(Dataset):
    def __init__(self):
        return

    def __len__(self):
        return 50

    def __getitem__(self, index):
        return torch.rand((3, 256, 256)), torch.randint(0, 2, (1, 256, 256))

    def loader(self, **args):
        return DataLoader(self, **args)

class FaceMaskDataset(Dataset):
    # remove aug_t also in training, add AugmentPipe
    def __init__(self, dataset_dir, csv_file, transf):
        self.dataset_dir = dataset_dir
        self.images = pd.read_csv(f'{dataset_dir}/{csv_file}', dtype='str')
        self.dataset_len = len(self.images)
        self.transf = transf if transf is not None else lambda x: x

    def __len__(self):
        return self.dataset_len

    def __getitem__(self, index):
        if torch.is_tensor(index):
            index = index.tolist()

        img_name = os.path.join(self.dataset_dir, self.images.iloc[index, 1])
        mask_name = os.path.join(self.dataset_dir, self.images.iloc[index, 2])

        img = read_image(img_name)
        mask = read_image(mask_name)
        mask = torch.div(mask, 255, rounding_mode='floor')

        img = self.transf(img)
        mask = self.transf(mask)

        return img.float(), mask.float()

    def loader(self, **args):
        return DataLoader(self, **args)

if __name__ == "__main__":
    dataset = FaceMaskDataset('../dataset/', 'maskffhq.csv')
    dloader = dataset.loader()
    for imgs, masks in dloader:
        cv2.imshow('img', imgs[0].numpy())
        cv2.waitKey(0)
        cv2.imshow('mask', masks[0].numpy())
        cv2.waitKey(0)
        break
