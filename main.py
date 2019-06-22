import time
import argparse

import torch.multiprocessing as mp

from PredNet import *
from ConvLSTM import *
from activations import *
from utils import *
from mp_train import train, test

parser = argparse.ArgumentParser()
# Multiprocessing
parser.add_argument('--num_processes',type=int,default=2,
                    help='Number of training processes to use.')
parser.add_argument('--seed',type=int, default=0,
                    help='Manual seed for torch random number generator')

# Training data
parser.add_argument('--dataset',choices=['KITTI','CCN'],default='KITTI',
                    help='Dataset to use')
parser.add_argument('--train_data_path',
                    default='../data/kitti_data/X_train.hkl',
                    help='Path to training images hkl file')
parser.add_argument('--train_sources_path',
                    default='../data/kitti_data/sources_train.hkl',
                    help='Path to training sources hkl file')
parser.add_argument('--val_data_path',
                    default='../data/kitti_data/X_val.hkl',
                    help='Path to validation images hkl file')
parser.add_argument('--val_sources_path',
                    default='../data/kitti_data/sources_val.hkl',
                    help='Path to validation sources hkl file')
parser.add_argument('--test_data_path',
                    default='../data/kitti_data/X_test.hkl',
                    help='Path to test images hkl file')
parser.add_argument('--test_sources_path',
                    default='../data/kitti_data/sources_test.hkl',
                    help='Path to test sources hkl file')
parser.add_argument('--seq_len',type=int,default=10,
                    help='Number of images in each kitti sequence')
parser.add_argument('--batch_size', type=int, default=4,
                    help='Samples per batch')
parser.add_argument('--num_iters', type=int, default=75000,
                    help='Number of optimizer steps before stopping')

# Models
parser.add_argument('--model_type', choices=['PredNet','ConvLSTM'],
                    default='PredNet', help='Type of model to use.')
# Hyperparameters for PredNet
parser.add_argument('--stack_sizes', type=int, nargs='+', default=[3,48,96,192],
                    help='number of channels in targets (A) and ' +
                         'predictions (Ahat) in each layer. ' +
                         'Length should be equal to number of layers')
parser.add_argument('--R_stack_sizes', type=int, nargs='+',
                    default=[3,48,96,192],
                    help='Number of channels in R modules. ' +
                         'Length should be equal to number of layers')
parser.add_argument('--A_kernel_sizes', type=int, nargs='+', default=[3,3,3],
                    help='Kernel sizes for each A module. ' +
                         'Length should be equal to (number of layers - 1)')
parser.add_argument('--Ahat_kernel_sizes', type=int, nargs='+',
                    default=[3,3,3,3], help='Kernel sizes for each Ahat' +
                    'module. Length should be equal to number of layers')
parser.add_argument('--R_kernel_sizes', type=int, nargs='+', default=[3,3,3,3],
                    help='Kernel sizes for each Ahat module' +
                         'Length should be equal to number of layers')
parser.add_argument('--use_satlu', type=str2bool, default=True,
                    help='Boolean indicating whether to use SatLU in Ahat.')
parser.add_argument('--satlu_act', default='hardtanh',
                    choices=['hardtanh','logsigmoid'],
                    help='Type of activation to use for SatLU in Ahat.')
parser.add_argument('--pixel_max', type=float, default=1.0,
                    help='Maximum output value for Ahat if using SatLU.')
parser.add_argument('--error_act', default='relu',
                    choices=['relu','sigmoid','tanh','hardsigmoid'],
                    help='Type of activation to use in E modules.')
parser.add_argument('--use_1x1_out', type=str2bool, default=False,
                    help='Boolean indicating whether to use 1x1 conv layer' +
                         'for output of ConvLSTM cells')
# Hyperparameters for ConvLSTM
parser.add_argument('--hidden_channels', type=int, default=192,
                    help='Number of channels in hidden states of ConvLSTM')
parser.add_argument('--kernel_size', type=int, default=3,
                    help='Kernel size in ConvLSTM')
parser.add_argument('--out_act', default='relu',
                    help='Activation for output layer of ConvLSTM cell')
# Hyperparameters shared by PredNet and ConvLSTM
parser.add_argument('--in_channels', type=int, default=3,
                    help='Number of channels in input images')
parser.add_argument('--LSTM_act', default='tanh',
                    choices=['relu','sigmoid','tanh','hardsigmoid'],
                    help='Type of activation to use in ConvLSTM.')
parser.add_argument('--LSTM_c_act', default='hardsigmoid',
                    choices=['relu','sigmoid','tanh','hardsigmoid'],
                    help='Type of activation for inner ConvLSTM (C_t).')
parser.add_argument('--bias', type=str2bool, default=True,
                    help='Boolean indicating whether to use bias units')
parser.add_argument('--FC', type=str2bool, default=False,
                    help='Boolean indicating whether to use fully connected' +
                         'convolutional LSTM cell')
parser.add_argument('--load_weights_from', default=None,
                    help='Path to saved weights')

# Optimization
parser.add_argument('--loss', default='E',choices=['E','MSE','L1'])
parser.add_argument('--learning_rate', type=float, default=0.001,
                    help='Fixed learning rate for Adam optimizer')
parser.add_argument('--lr_steps', type=int, default=1,
                    help='num times to decrease learning rate by factor of 0.1')
parser.add_argument('--layer_lambdas', type=float,
                    nargs='+', default=[1.0,0.0,0.0,0.0],
                    help='Weight of loss on error of each layer' +
                         'Length should be equal to number of layers')

# Output options
parser.add_argument('--results_dir', default='../results/train_results',
                    help='Results subdirectory to save results')
parser.add_argument('--out_data_file', default='results.json',
                    help='Name of output data file with training loss data')
parser.add_argument('--checkpoint_path',default=None,
                    help='Path to output saved weights.')
parser.add_argument('--record_loss_every', type=int, default=20,
                    help='iters before printing and recording loss')

if __name__ == '__main__':
    start_train_time = time.time()

    args = parser.parse_args()
    print(args)

    use_cuda = torch.cuda.is_available()
    device = torch.device("cuda:0" if use_cuda else "cpu")
    dataloader_kwargs = {'pin_memory': True} if use_cuda else {}
    print("CUDA is available: ", use_cuda)
    print("MKL is available: ", torch.backends.mkl.is_available())
    print("MKL DNN is available: ", torch._C.has_mkldnn)

    torch.manual_seed(args.seed)
    mp.set_start_method('spawn')

    if args.model_type == 'PredNet':
        model = PredNet(args.in_channels,args.stack_sizes,args.R_stack_sizes,
                        args.A_kernel_sizes,args.Ahat_kernel_sizes,
                        args.R_kernel_sizes,args.use_satlu,args.pixel_max,
                        args.satlu_act,args.error_act,args.LSTM_act,
                        args.LSTM_c_act,args.bias,args.use_1x1_out,args.FC,
                        device)
    elif args.model_type == 'ConvLSTM':
        model = ConvLSTM(args.in_channels,args.hidden_channels,args.kernel_size,
                         args.LSTM_act,args.LSTM_c_act,args.out_act,
                         args.bias,args.FC,device)

    if args.load_weights_from is not None:
        model.load_state_dict(torch.load(args.load_weights_from))
    model.to(device)
    model.share_memory() # grads allocated lazily, so they are not shared here

    processes = []
    for rank in range(args.num_processes):
        p = mp.Process(target=train,
                       args=(rank, args, model, device, dataloader_kwargs))
        p.start()
        processes.append(p)
    for p in processes:
        p.join()

    if args.checkpoint_path is not None:
        torch.save(model.state_dict(),
                   args.checkpoint_path)

    test(args,model,device,dataloader_kwargs)

    print("Total training time: ", time.time() - start_train_time)