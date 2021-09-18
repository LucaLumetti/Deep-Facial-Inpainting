## People
The people involved in the project are:
- Luca Lumetti (244577@studenti.unimore.it)
- Matteo Di Bartolomeo (241469@studenti.unimore.it)
- Federico Silvestri (243938@studenti.unimore.it)

## Overview

Our project aims to remove face masks over a person’s face, by reconstructing
the covered part of the face. To have a more precise reconstruction of the missing
parts (mouth and nose) behind the mask, we plan to use a second photo of the
same person without the mask as a reference during the facial reconstruction
process. There are no constraints on the quality of the reference photo, for
instance the face can be taken from a different point of view than the first one.
To sum up, given as input an image containing a person’s face partially covered
by a medical mask and another photo of the same person without any occlusions,
the output will be the first image with the mask-covered parts, mouth and nose,
reconstructed.
Future development could lead to generalizing the occlusion caused by the mask
to any type of occlusion possible.

## Pipeline
The pipeline will be structured as follows:
- Detect the mask in the first image by using classical image-processing operators, like edge detection and/or segmentation algorithms.
- Fix face orientation in the reference image by using a geometric-based algorithm, like the thin-plate spline transformation.
- Reconstruct the missing parts of the face in the first image by using a GAN network with a contrastive learning approach. (Deep learning network with a retrieval component)