import argparse
import logging

import torch
import torch.nn as nn
from torch import cuda
from torch.autograd import Variable
from torch.utils.data import DataLoader,Dataset

import torchvision
import torchvision.datasets as dset
import torchvision.transforms as transforms
import torchvision.utils
from PIL import Image

import torch.nn.functional as F

import matplotlib.pyplot as plt
import numpy as np
import random

from custom_transform import CustomResize
from custom_transform import CustomToTensor

from AD_Dataset import AD_Dataset
from cnn_3d_with_ae import CNN

logging.basicConfig(
    format='%(asctime)s %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S', level=logging.INFO)

parser = argparse.ArgumentParser(description="Starter code for CNN .")

parser.add_argument("--epochs", default=20, type=int,
                    help="Epochs through the data. (default=20)")  
parser.add_argument("--learning_rate", "-lr", default=1e-3, type=float,
                    help="Learning rate of the optimization. (default=0.01)")
parser.add_argument('--weight-decay', '--wd', default=1e-4, type=float,
                    metavar='W', help='weight decay (default: 1e-4)')         
parser.add_argument("--batch_size", default=1, type=int,
                    help="Batch size for training. (default=1)")
parser.add_argument("--gpuid", default=[0], nargs='+', type=int,
                    help="ID of gpu device to use. Empty implies cpu usage.")
parser.add_argument("--autoencoder", default=True, type=bool,
                    help="Whether to use the parameters from pretrained autoencoder.")
parser.add_argument("--num_classes", default=2, type=int,
                    help="The number of classes, 2 or 3.")
# feel free to add more arguments as you need



def main(options):
    # Path configuration
    if options.num_classes == 2:
        TRAINING_PATH = 'train_2classes.txt'
        TESTING_PATH = 'test_2classes.txt'
    else:
        TRAINING_PATH = 'train.txt'
        TESTING_PATH = 'test.txt'
    IMG_PATH = './ImageNoSkull'

    trg_size = (110, 110, 110)
    
    transformations = transforms.Compose([CustomResize("CNN", trg_size),
                                          CustomToTensor("CNN")
                                        ])

    dset_train = AD_Dataset(IMG_PATH, TRAINING_PATH, transformations)
    dset_test = AD_Dataset(IMG_PATH, TESTING_PATH, transformations)

    # Use argument load to distinguish training and testing

    train_loader = DataLoader(dset_train,
                              batch_size = options.batch_size,
                              shuffle = True,
                              num_workers = 4,
                              drop_last = True
                              )

    test_loader = DataLoader(dset_test,
                             batch_size = options.batch_size,
                             shuffle = False,
                             num_workers = 4,
                             drop_last=True
                             )

    use_cuda = (len(options.gpuid) >= 1)
    # if options.gpuid:
    #     cuda.set_device(options.gpuid[0])

    # Training process
    model = CNN(options.num_classes)

    if use_cuda > 0:
        model = nn.DataParallel(model, device_ids=options.gpuid).cuda()
    else:
        model.cpu()

    if options.autoencoder:
        pretrained_ae = torch.load("./autoencoder_pretrained_model19")
        model = model.load_ae(pretrained_ae)

    criterion = torch.nn.NLLLoss()

    lr = options.learning_rate
    optimizer = torch.optim.Adam(model.parameters(), lr, weight_decay=options.weight_decay)

    # main training loop
    for epoch_i in range(options.epochs):
        logging.info("At {0}-th epoch.".format(epoch_i))
        train_loss = 0.0
        correct_cnt = 0.0
        for it, train_data in enumerate(train_loader):
            data_dic = train_data

            if use_cuda:
                imgs, labels = Variable(data_dic['image']).cuda(), Variable(data_dic['label']).cuda() 
            else:
                imgs, labels = Variable(data_dic['image']), Variable(data_dic['label'])

            # add channel dimension: (batch_size, D, H ,W) to (batch_size, 1, D, H ,W)
            # since 3D convolution requires 5D tensors
            img_input = imgs#.unsqueeze(1)

            integer_encoded = labels.data.cpu().numpy()
            # target should be LongTensor in loss function
            ground_truth = Variable(torch.from_numpy(integer_encoded)).long()
            if use_cuda:
                ground_truth = ground_truth.cuda()
            train_output = model(img_input)
            train_prob_predict = F.softmax(train_output, dim=1)
            _, predict = train_prob_predict.topk(1)
            loss = criterion(train_output, ground_truth)

            train_loss += loss
            correct_this_batch = (predict.squeeze(1) == ground_truth).sum()
            correct_cnt += correct_this_batch
            accuracy = float(correct_this_batch) / len(ground_truth)
            logging.info("batch {0} training loss is : {1:.5f}".format(it, loss.data[0]))
            logging.info("batch {0} training accuracy is : {1:.5f}".format(it, accuracy))
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

        train_avg_loss = train_loss / (len(dset_train) / options.batch_size)
        train_avg_acu = float(correct_cnt) / len(dset_train)
        logging.info("Average training loss is {0:.5f} at the end of epoch {1}".format(train_avg_loss.data[0], epoch_i))
        logging.info("Average training accuracy is {0:.5f} at the end of epoch {1}".format(train_avg_acu, epoch_i))
        
        # validation -- this is a crude esitmation because there might be some paddings at the end
        dev_loss = 0.0
        correct_cnt = 0.0
        model.eval()
        for it, test_data in enumerate(test_loader):
            data_dic = test_data

            if use_cuda:
                imgs, labels = Variable(data_dic['image'], volatile=True).cuda(), Variable(data_dic['label'], volatile=True).cuda() 
            else:
                imgs, labels = Variable(data_dic['image'], volatile=True), Variable(data_dic['label'], volatile=True)

            img_input = imgs#.unsqueeze(1)
            integer_encoded = labels.data.cpu().numpy()
            ground_truth = Variable(torch.from_numpy(integer_encoded), volatile=True).long()
            if use_cuda:
                ground_truth = ground_truth.cuda()
            test_output = model(img_input)
            test_prob_predict = F.softmax(test_output, dim=1)
            _, predict = test_prob_predict.topk(1)
            loss = criterion(test_output, ground_truth)
            dev_loss += loss
            correct_this_batch = (predict.squeeze(1) == ground_truth).sum()
            correct_cnt += (predict.squeeze(1) == ground_truth).sum()
            accuracy = float(correct_this_batch) / len(ground_truth)
            logging.info("batch {0} dev loss is : {1:.5f}".format(it, loss.data[0]))
            logging.info("batch {0} dev accuracy is : {1:.5f}".format(it, accuracy))

        dev_avg_loss = dev_loss / (len(dset_test) / options.batch_size)
        dev_avg_acu = float(correct_cnt) / len(dset_test)
        logging.info("Average validation loss is {0:.5f} at the end of epoch {1}".format(dev_avg_loss.data[0], epoch_i))
        logging.info("Average validation accuracy is {0:.5f} at the end of epoch {1}".format(dev_avg_acu, epoch_i))

        if (epoch_i+1)%20==0:
            torch.save(model.state_dict(), open("3DCNN_model_" + str(epoch_i), 'wb'))


if __name__ == "__main__":
  ret = parser.parse_known_args()
  options = ret[0]
  if ret[1]:
    logging.warning("unknown arguments: {0}".format(parser.parse_known_args()[1]))
  main(options)