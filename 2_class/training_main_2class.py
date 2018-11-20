import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
from torch.utils.data import DataLoader
import matplotlib.pyplot as plt
import numpy as np
import sys
import argparse
from tqdm import tqdm

from data_loading_2class import DeepDriveDataset, load_datasets
from fcn_2class import train, test
from vgg16_2class import VGG16



"""
Main function to create the dataset object, initialize the model
and train it
"""
if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='A training module for an FCN for the Berkley Deep Drive Dataset')
    parser.add_argument('--load', '-l', action = "store", type=str, help= 'A file location to load the model from',
                         dest = 'load_dir', default = '')
    parser.add_argument('--test', '-t', action = "store_true", help = "A flag to say that we are testing the model only")
    parser.add_argument('--save-to', '-s', type = str, help = "A file location to store the model", dest = "save_dir", required=True)
    parser.add_argument('--log_iters', '-log', type = int, help = "The spacing between log printouts for training and testing", default = 7200)
    parser.add_argument('-lr', type = float, help = "the learning rate to use", default = .001)
    parser.add_argument('--cuda', '-c', action = "store_true", help = "Flag to use cuda for training and testing")
    parser.add_argument('--per_class', action="store_true", help="Flag to output per class data during training")
    parser.add_argument('--batch_size', type = int, action= "store", help = "set the batch size for training and testing", default=1)
    parser.add_argument('--visualize_output', "-vis", action = "store_true", help = "visualize the output every <log_iters> for testing")
    parser.add_argument('--use_crf', "-crf", action = "store_true", help = "postprocess data with the CRF for testing")

    args = parser.parse_args()

    # #TODO parse command line arguments
    DEFAULT_EPOCHS = 1000
    epochs = DEFAULT_EPOCHS
    USE_CUDA = args.cuda
    DEFAULT_DEVICE = "cuda" if args.cuda else "cpu"
    DEFAULT_BATCH = args.batch_size

    print("using " + DEFAULT_DEVICE + " ---- batch_size = " + str(DEFAULT_BATCH))

    #img_path = "/home/arjun/MIT/6.867/project/bdd100k_images/bdd100k/images/100k"
    #test_path = "/home/arjun/MIT/6.867/project/bdd100k_drivable_maps/bdd100k/drivable_maps/labels"
    img_path = "C:/Users/sarah/Documents/6.867 Project/bdd100k_images/bdd100k_images/bdd100k/images/100k"
    test_path = "C:/Users/sarah/Documents/6.867 Project/bdd100k_drivable_maps/bdd100k_drivable_maps/bdd100k/drivable_maps/labels"

    print("Initializing Dataset ... ")
    #load datasets
    train_dataset, test_dataset = load_datasets(img_path, test_path)
    train_loader = DataLoader(train_dataset, batch_size = DEFAULT_BATCH, shuffle = False,
                             num_workers = 1 if USE_CUDA else 0, pin_memory = USE_CUDA)
    test_loader = DataLoader(test_dataset, batch_size = DEFAULT_BATCH, shuffle = False,
                             num_workers = 1 if USE_CUDA else 0, pin_memory = USE_CUDA)
    

    print("Initializing FCN for Segmentation...")

    #intialize model
    segmentation_model = VGG16(args.save_dir)

    if not args.load_dir == '':
        with open(args.load_dir, 'rb') as f:
            segmentation_model.load_state_dict(torch.load(f))
    
    # push model to either cpu or gpu
    segmentation_model.to(torch.device(DEFAULT_DEVICE))
    if not args.test:
        print("Initializing Optimizer...")
        #intialize optimizer
        optimizer = optim.Adam(segmentation_model.parameters(), lr = args.lr)
        print("Successful initialization!")

        #train the model for a set number of epochs
        for epoch in range(epochs):
            train(segmentation_model, torch.device(DEFAULT_DEVICE), train_loader, optimizer, epoch,
                             log_spacing = args.log_iters, per_class=args.per_class)
            segmentation_model.save()
            test(segmentation_model, torch.device(DEFAULT_DEVICE), test_loader, use_crf = False, iters_per_log = args.log_iters)

    else:
        print("Successful initialization!")
        print("testing...")
        test(segmentation_model, torch.device(DEFAULT_DEVICE), test_loader, use_crf = args.use_crf, iters_per_log=args.log_iters, visualize = args.visualize_output)


