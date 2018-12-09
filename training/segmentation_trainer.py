import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import matplotlib.pyplot as plt
import numpy as np
from tqdm import tqdm
from PIL import Image
from utils.progress_bar import ProgressBar
# our own code imports
from utils.crf import crf_batch_postprocessing

class SegmentationTrainer:
    """
    Class to train segmentation model
    """
    def __init__(self, model, device, train_loader, test_loader, optimizer, data_stats,
                 num_classes = 3, log_spacing = 100, save_spacing = 100, per_class = False):
        self.model = model
        self.device = device
        self.train_loader = train_loader
        self.test_loader = test_loader
        self.num_classes = num_classes
        self.optimizer = optimizer
        self.log_spacing = log_spacing
        self.save_spacing = save_spacing
        self.per_class = per_class
        self.data_statistics = data_stats

    def train(self, epoch, start_index = 0):
        """
        Args:
            model (nn.Module): the FCN pytorch model
            device (torch.device): represents if we are running this on GPU or CPU
            optimizer (torch.optim): the optimization object that trains the network. Ex: torch.optim.Adam(modle.parameters())
            train_loader (torch.utils.data.DataLoader): the pytorch object that contains all training data and targets
            epoch (int): the epoch number we are on
            log_spacing (int): prints training statistics to display every <lo 
            
           _spacing> batches
            save_spacing (int): saves most recent version of model every <save_spacing> batches
            per_class (boolean): true if want class-level statistics printed. false otherwise
        """
        progress_bar = ProgressBar("Train", len(self.train_loader), self.train_loader.batch_size)
        self.model.train()  # puts it in training mode
        class_correct =  [0] * self.num_classes
        class_jacard_or = [0] * self.num_classes
        sum_loss = 0
        num_batches_since_log = 0
        loss_func = nn.CrossEntropyLoss(reduction = "none")
        # run through data in batches, train network on each batch
        for batch_idx, (_, data, target) in tqdm(enumerate(self.train_loader)):
            #progress_bar.make_progress()
            if batch_idx < start_index: continue
            loss_vec = torch.zeros((self.num_classes), dtype = torch.float32)
            data, target = data.to(self.device), target.to(self.device)
            self.optimizer.zero_grad()  # reset gradient to 0 (so doesn't accumulate)
            output = self.model(data)  # runs batch through the model
            loss = loss_func(output, target)  # compute loss of output

            # convert into 1 channel image with predicted class values 
            pred = torch.argmax(output, dim = 1, keepdim = False)
            #assert(pred.shape == (self.train_loader.batch_size, 1280, 720)), "got incorrect shape of: " + str(pred.shape)

            # record pixel measurements
            for i in range(self.num_classes):
                correct_pixels = torch.where(
                    pred.byte().eq(torch.ones(pred.shape, dtype = torch.uint8).to(self.device) * i)
                    & target.view_as(pred).byte().eq(torch.ones(pred.shape, dtype = torch.uint8).to(self.device) * i),
                    torch.ones(pred.shape, dtype = torch.uint8).to(self.device),
                    torch.zeros(pred.shape, dtype = torch.uint8).to(self.device)).sum().item()
                class_correct[i] += correct_pixels
                jaccard_or_pixels = torch.where(
                    pred.byte().eq(torch.ones(pred.shape, dtype = torch.uint8).to(self.device) * i)
                    | target.view_as(pred).byte().eq(torch.ones(pred.shape, dtype = torch.uint8).to(self.device) * i),
                    torch.ones(pred.shape, dtype = torch.uint8).to(self.device),
                    torch.zeros(pred.shape, dtype = torch.uint8).to(self.device)).sum().item()
                class_jacard_or[i] += jaccard_or_pixels

            get_per_class_loss(loss, target, loss_vec)
            loss = torch.sum(loss_vec)
            self.model.train_stats.per_class_loss.append(loss_vec)

            sum_loss += loss.item()
            loss.backward()  # take loss object and calculate gradient; updates optimizer
            self.optimizer.step()  # update model parameters with loss gradient

            #update per-class accuracies
            get_per_class_accuracy(pred, target, self.model.train_stats.confusion)

            if batch_idx % self.log_spacing == 0:
                self.model.train_stats.per_class_accuracy.append(np.diagonal(self.model.train_stats.confusion).copy())
                print("Loss Vec: {}".format(loss_vec))
                self.print_log(class_correct, class_jacard_or, sum_loss, batch_idx + 1, self.train_loader.batch_size,
                          "Training Set", self.per_class, self.model.train_stats.confusion)

            if batch_idx % self.save_spacing == 0:
                print('Saving Model to: ' + str(self.model.save_dir))
                self.model.save()

    def test(self, dataset_name= "Test set", use_crf = True, iters_per_log = 100, visualize = False, use_prior = True):
        self.model.eval()
        test_loss = 0
        class_correct =  [0] * self.num_classes
        class_jacard_or = [0] * self.num_classes
        loss_func = nn.CrossEntropyLoss()
        batches_done = 0
        progress_bar = ProgressBar("Test", len(self.train_loader), self.train_loader.batch_size)
        with torch.no_grad():            
            # calculate an UNBIASED prior
            prior = self.data_statistics.get_distribution().to(self.device)
            for i in range(self.num_classes):
                prior[i] = prior[i] / (torch.mean(prior[i]))  #  scales relative probs to have mean of 1
            normalization = torch.sum(prior, dim = 0)  # sum along classes
            prior /= normalization
            prior = torch.ones(prior.shape).to(self.device) - prior

            for batch_idx, (raw_samples, data, target) in tqdm(enumerate(self.test_loader)):  # runs through trainer
                data, target = data.to(self.device), target.to(self.device)
                #progress_bar.make_progress()
                output = self.model(data)
                if use_prior:
                    output = np.e**(output)
                    for i in range(len(output)): # could be multiple images in output batch
                        output[i] = output[i] - prior
                        output[i] = torch.sigmoid(output[i])
                        normalization = torch.sum(output[i], dim = 0)
                        output[i] /= normalization
                        output = np.log(output)

                if use_crf:
                    output = crf_batch_postprocessing(raw_samples, output, self.num_classes)

                output = output.to(self.device)
                test_loss += loss_func(output, target).item()

                #convert into 1 channel image with values 
                pred = torch.argmax(output, dim = 1, keepdim = False)
                #assert(pred.shape == (self.test_loader.batch_size, 1280, 720)), "got incorrect shape of: " + str(pred.shape)

                # record pixel measurements
                for i in range(self.num_classes):
                    correct_pixels = torch.where(pred.byte().eq(torch.ones(pred.shape, dtype = torch.uint8).to(self.device)*i)
                                                 & target.view_as(pred).byte().eq(torch.ones(pred.shape, dtype = torch.uint8).to(self.device)*i),
                                                 torch.ones(pred.shape, dtype=torch.uint8).to(self.device),
                                                 torch.zeros(pred.shape, dtype=torch.uint8).to(self.device)).sum().item()
                    class_correct[i] += correct_pixels
                    jaccard_or_pixels = torch.where(pred.byte().eq(torch.ones(pred.shape, dtype = torch.uint8).to(self.device)*i)
                                                 | target.view_as(pred).byte().eq(torch.ones(pred.shape, dtype = torch.uint8).to(self.device)*i),
                                                 torch.ones(pred.shape, dtype=torch.uint8).to(self.device),
                                                 torch.zeros(pred.shape, dtype=torch.uint8).to(self.device)).sum().item()
                    class_jacard_or[i] += jaccard_or_pixels

                get_per_class_accuracy(pred, target, self.model.test_stats.confusion)
                batches_done += 1

                if(batches_done % self.log_spacing == 0):
                    self.model.test_stats.per_class_accuracy.append(np.diagonal(self.model.test_stats.confusion).copy())
                    self.print_log(class_correct, class_jacard_or, test_loss, batches_done, self.test_loader.batch_size, dataset_name, True, self.model.test_stats.confusion, test = True)
                    print("saving model to {}".format(self.model.save_dir))
                    self.model.save()

                    if visualize:
                        visualize_output(pred, target, raw_samples)



    def print_log(self, class_correct_pixels, class_jacard_or, loss, num_samples, batch_size, name, use_acc_dict = False, acc_dict = None, test = False):
        loss = loss/(num_samples*batch_size)
        total_samples = num_samples*batch_size*1280*720
        accuracy = 100. * sum(class_correct_pixels) / total_samples
        jaccard_accuracy = np.mean(list(map(lambda x, y: x/y, class_correct_pixels, class_jacard_or)))
        print('\n--------------------------------------------------------------')
        print('\n{}: Average loss: {:.4f}, Accuracy: {}/{} ({:.0f}%), Jaccard: {}\n'.format(
            name, loss, sum(class_correct_pixels), total_samples, accuracy, jaccard_accuracy))
        
        if test:
            self.model.test_stats.loss.append(loss)
            self.model.test_stats.accuracy.append(accuracy)
            try:
                self.model.test_stats.jaccard_accuracy.append(jaccard_accuracy)
            except AttributeError:
                pass  # models trained before jaccard accuracy was involved
        else:
            self.model.train_stats.loss.append(loss)
            self.model.train_stats.accuracy.append(accuracy)
            try:
                self.model.test_stats.jaccard_accuracy.append(jaccard_accuracy)
            except AttributeError:
                pass

        if use_acc_dict:
            if acc_dict.shape[0] == 3:
                print('\n Class |  Samples  | % Class 0 | % Class 1 | %Class 2 |')
                for class_type in range(len(acc_dict)):
                    total = acc_dict[class_type][0] + acc_dict[class_type][1] + acc_dict[class_type][2]
                    if total == 0: 
                        print(' {}     |     0     |    n/a    |    n/a    |    n/a    |'.format(class_type))
                    else: 
                        print(' {}     | {} |   {}   |   {}   |   {}   |'.format(class_type, total, 100*acc_dict[class_type][0]/total, 
                        100*acc_dict[class_type][1]/total, 100*acc_dict[class_type][2]/total))

            else:
                print('\n Class |  Samples  | % Class 0 | % Class 1 |')
                for class_type in range(len(acc_dict)):
                    total = acc_dict[class_type][0] + acc_dict[class_type][1]
                    if total == 0:
                        print(' {}     |     0     |    n/a    |    n/a    |'.format(class_type))
                    else:
                        print(' {}     | {} |   {}   |   {}   |'.format(class_type, total, 100*acc_dict[class_type][0]/total,
                        100*acc_dict[class_type][1]/total))
                
            print('--------------------------------------------------------------')

def get_per_class_loss(loss, target, loss_vec):
    for i in range(len(loss_vec)):
        mask = target.eq(i)
        total_num = torch.sum(mask)
        if(total_num.item() > 0):
            loss_vec[i] = torch.sum(torch.masked_select(loss, mask))/total_num.item()
        else:
            loss_vec[i] = torch.sum(torch.masked_select(loss, mask))


def get_per_class_accuracy(pred, target, acc_dict):
    """
    Takes in a batch of predictions and targets as pytorch tensors, as well as an accuracy matrix. Mutates the 
    accuracy matrix by summing the prediction-target pixel-wise accuracies in each entry.

    Args:
        pred (torch.tensor): 3D tensor. Axis 0 has each image output, axes 1 and 2 define the predicted output; each entry
        will be 0, 1, or 2 depending on the class
        target (torch.tensor): same as pred, but the correct target
        acc_dict (2d list): a 3 by 3 matrix containing accuracies of pixel ratings. acc_dict[0][1] indicates pixels that the 
            prediction labeled class 0, but the target labeled class 1
    """
    prediction_numpy, target_numpy = pred.cpu().numpy(), target.cpu().numpy()

    def prediction_error(predicted_label, target_label):
        """
        To get the number of times our output label is 0, but the target is 2, we would call
        prediction_error(predicted_label = 0, target_label = 2)
        """
        return len(np.where(np.logical_not(np.logical_or(prediction_numpy - predicted_label, target_numpy - target_label)))[0])

    for i in range(len(acc_dict)):
        for j in range(len(acc_dict[i])):
            acc_dict[j][i] += prediction_error(i, j)

  


def visualize_output(pred, target, image):
    """
    Args:
        pred (torch.tensor): 3D tensor. Axis 0 has each image output, axes 1 and 2 define the predicted output; each entry
            will be 0, 1, or 2 depending on the class
        target (torch.tensor): same as pred, but the correct target
    """

    prediction_numpy, target_numpy, raw_image = pred.cpu().data.numpy()[0,:,:], target.cpu().data.numpy()[0,:,:], image.cpu().data.numpy()[0,:,:,:]
    total_image = (np.hstack((prediction_numpy, target_numpy))*100)
    total_image = np.array(total_image, dtype = np.uint8).T
    real_image = np.array(raw_image, dtype = np.uint8).T

    # show actual target
    image = Image.fromarray(total_image, "L")
    image2 = Image.fromarray(real_image)
    image.show()
    image2.show()


