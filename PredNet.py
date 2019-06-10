# PredNet architecture

import torch
import torch.nn.functional as F
import torch.nn as nn

from activations import Hardsigmoid, SatLU
from utils import *

# Convolutional LSTM cell used for R cells
class RCell(nn.Module):
    """
    Modified version of 2d convolutional lstm as described in the paper:
    Title: Convolutional LSTM Network: A Machine Learning Approach for
           Precipitation Nowcasting
    Authors: Xingjian Shi, Zhourong Chen, Hao Wang, Dit-Yan Yeung, Wai-kin Wong,
             Wang-chun Woo
    arxiv: https://arxiv.org/abs/1506.04214

    Changes are made according to PredNet paper: LSTM is not "fully connected",
    in the sense that i,f, and o do not depend on C.
    """
    def __init__(self, in_channels, hidden_channels, kernel_size,
                 LSTM_act, LSTM_c_act, is_last, bias=True, use_out=True,
                 FC=False):
        super(RCell, self).__init__()
        self.in_channels = in_channels
        self.hidden_channels = hidden_channels
        self.kernel_size = kernel_size
        self.is_last = is_last # bool
        self.bias = bias
        self.use_out = use_out
        self.FC = FC # use fully connected ConvLSTM

        # Activations
        self.LSTM_act = get_activation(LSTM_act)
        self.LSTM_c_act = get_activation(LSTM_c_act)

        self.stride = 1 # Stride always 1 for simplicity
        self.dilation = 1 # Dilation always 1 for simplicity
        _pad = 0 # Padding done manually in forward()
        self.groups = 1 # Groups always 1 for simplicity

        # Convolutional layers
        self.Wxi = nn.Conv2d(in_channels,hidden_channels,kernel_size,
                             self.stride,_pad,self.dilation,
                             self.groups,self.bias)
        self.Whi = nn.Conv2d(hidden_channels,hidden_channels,
                             kernel_size,self.stride,_pad,
                             self.dilation,self.groups,self.bias)
        self.Wxf = nn.Conv2d(in_channels,hidden_channels,kernel_size,
                             self.stride,_pad,self.dilation,
                             self.groups,self.bias)
        self.Whf = nn.Conv2d(hidden_channels,hidden_channels,
                             kernel_size,self.stride,_pad,
                             self.dilation,self.groups,self.bias)
        self.Wxc = nn.Conv2d(in_channels,hidden_channels,kernel_size,
                             self.stride,_pad,self.dilation,
                             self.groups,self.bias)
        self.Whc = nn.Conv2d(hidden_channels,hidden_channels,
                             kernel_size,self.stride,_pad,
                             self.dilation,self.groups,self.bias)
        self.Wxo = nn.Conv2d(in_channels,hidden_channels,kernel_size,
                             self.stride,_pad,self.dilation,
                             self.groups,self.bias)
        self.Who = nn.Conv2d(hidden_channels,hidden_channels,
                             kernel_size,self.stride,_pad,
                             self.dilation,self.groups,self.bias)

        # Extra layers for fully connected
        if FC:
            self.Wci = nn.Conv2d(hidden_channels,hidden_channels,kernel_size,
                                 self.stride,_pad,self.dilation,
                                 self.groups,self.bias)
            self.Wcf = nn.Conv2d(hidden_channels,hidden_channels,kernel_size,
                                 self.stride,_pad,self.dilation,
                                 self.groups,self.bias)
            self.Wco = nn.Conv2d(hidden_channels,hidden_channels,kernel_size,
                                 self.stride,_pad,self.dilation,
                                 self.groups,self.bias)
        # 1 x 1 convolution for output
        if use_out:
            self.out = nn.Conv2d(hidden_channels,hidden_channels,1,1,0,1,1)

    def forward(self, E, R_lp1, hidden):
        H_tm1, C_tm1 = hidden

        # Upsample R_lp1
        if not self.is_last:
            target_size = (E.shape[2],E.shape[3])
            R_up = F.interpolate(R_lp1,target_size)
            x_t = torch.cat((E,R_up),dim=1) # cat on channel dim
        else:
            x_t = E

        # Manual zero-padding to make H,W same
        in_height = x_t.shape[-2]
        in_width = x_t.shape[-1]
        padding = get_pad_same(in_height,in_width,self.kernel_size)
        x_t_pad = F.pad(x_t,padding)
        H_tm1_pad = F.pad(H_tm1,padding)
        C_tm1_pad = F.pad(C_tm1,padding)

        # No dependence on C for i,f,o?
        if not self.FC:
            i_t = self.LSTM_act(self.Wxi(x_t_pad) + self.Whi(H_tm1_pad))
            f_t = self.LSTM_act(self.Wxf(x_t_pad) + self.Whf(H_tm1_pad))
            C_t = f_t*C_tm1 + i_t*self.LSTM_c_act(self.Wxc(x_t_pad) + \
                                                  self.Whc(H_tm1_pad))
            o_t = self.LSTM_act(self.Wxo(x_t_pad) + self.Who(H_tm1_pad))
            H_t = o_t*self.LSTM_act(C_t)
        else:
            i_t = self.Wxi(x_t_pad) + self.Whi(H_tm1_pad) + self.Wci(C_tm1_pad)
            i_t = self.LSTM_act(i_t)

            f_t = self.Wxf(x_t_pad) + self.Whf(H_tm1_pad) + self.Wcf(C_tm1_pad)
            f_t = self.LSTM_act(f_t)

            C_t = self.Wxc(x_t_pad) + self.Whc(H_tm1_pad)
            C_t = f_t*C_tm1 + i_t*self.LSTM_c_act(C_t)
            C_t_pad = F.pad(C_t,padding)

            o_t = self.Wxo(x_t_pad) + self.Who(H_tm1_pad) + self.Wco(C_t_pad)
            o_t = self.LSTM_act(o_t)

            H_t = o_t*self.LSTM_act(C_t)
        if self.use_out:
            R_t = self.out(H_t)
            R_t = self.LSTM_act(R_t)
        else:
            R_t = H_t

        return R_t, (H_t,C_t)

# A cells = [Conv,ReLU,MaxPool]
class ACell(nn.Module):
    def __init__(self,in_channels,out_channels,
                 conv_kernel_size,conv_bias):
        super(ACell,self).__init__()

        # Hyperparameters
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.conv_kernel_size = conv_kernel_size
        self.conv_bias = conv_bias
        conv_stride = 1 # always 1 for simplicity
        _conv_pad = 0 # padding done manually
        conv_dilation = 1 # always 1 for simplicity
        conv_groups = 1 # always 1 for simplicity
        pool_kernel_size = 2 # always 2 for simplicity

        # Parameters
        self.conv =  nn.Conv2d(in_channels,out_channels,
                               conv_kernel_size,conv_stride,
                               _conv_pad,conv_dilation,conv_groups,
                               conv_bias)
        self.relu = nn.ReLU()
        self.max_pool = nn.MaxPool2d(pool_kernel_size)

    def forward(self,E_lm1):
        # Manual padding to keep H,W the same
        in_height = E_lm1.shape[2]
        in_width = E_lm1.shape[3]
        padding = get_pad_same(in_height,in_width,self.conv_kernel_size)
        E_lm1 = F.pad(E_lm1,padding)
        # Compute A
        A = self.conv(E_lm1)
        A = self.relu(A)
        A = self.max_pool(A)
        return A

# Ahat cell = [Conv,ReLU]
class AhatCell(nn.Module):
    def __init__(self,in_channels,out_channels,
                 conv_kernel_size,conv_bias,
                 satlu_act='hardtanh',use_satlu=False,pixel_max=1.0):
        super(AhatCell,self).__init__()
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.conv_kernel_size = conv_kernel_size
        self.conv_bias = conv_bias
        self.satlu_act = satlu_act
        self.use_satlu = use_satlu
        self.pixel_max = pixel_max

        conv_stride = 1 # always 1 for simplicity
        conv_pad_ = 0 # padding done manually
        conv_dilation = 1 # always 1 for simplicity
        conv_groups = 1 # always 1 for simplicity

        # Parameters
        self.conv =  nn.Conv2d(in_channels,out_channels,
                               conv_kernel_size,conv_stride,
                               conv_pad_,conv_dilation,conv_groups,
                               conv_bias)
        self.relu = nn.ReLU()
        if use_satlu:
            self.satlu = SatLU(satlu_act,self.pixel_max)

    def forward(self,R_l):
        # Manual padding to keep dims the same
        in_height = R_l.shape[2]
        in_width = R_l.shape[3]
        padding = get_pad_same(in_height,in_width,self.conv_kernel_size)
        # Compute A_hat
        R_l = F.pad(R_l,padding)
        A_hat = self.conv(R_l)
        A_hat = self.relu(A_hat)
        if self.use_satlu:
            A_hat = self.satlu(A_hat)
        return A_hat

# E Cell = [subtract,ReLU,Concatenate]
class ECell(nn.Module):
    def __init__(self,error_act):
        super(ECell,self).__init__()
        self.act = get_activation(error_act)
    def forward(self,A,A_hat):
        positive = self.act(A - A_hat)
        negative = self.act(A_hat - A)
        E = torch.cat((positive,negative),dim=1) # cat on channel dim
        return E

# PredNet
class PredNet(nn.Module):
    def __init__(self,in_channels,stack_sizes,R_stack_sizes,
                 A_kernel_sizes,Ahat_kernel_sizes,R_kernel_sizes,
                 use_satlu,pixel_max,satlu_act,error_act,
                 LSTM_act,LSTM_c_act,bias=True,
                 use_1x1_out=True,FC=False,device='cpu'):
        super(PredNet,self).__init__()
        self.in_channels = in_channels
        self.stack_sizes = stack_sizes
        self.R_stack_sizes = R_stack_sizes
        self.A_kernel_sizes = A_kernel_sizes
        self.Ahat_kernel_sizes = Ahat_kernel_sizes
        self.R_kernel_sizes = R_kernel_sizes
        self.use_satlu = use_satlu
        self.pixel_max = pixel_max
        self.satlu_act = satlu_act
        self.error_act = error_act
        self.LSTM_act = LSTM_act
        self.LSTM_c_act = LSTM_c_act
        self.bias = bias
        self.use_1x1_out=use_1x1_out
        self.FC = FC # use fully connected ConvLSTM
        self.device = device

        # Make sure consistent number of layers
        self.nb_layers = len(stack_sizes)
        msg = "len(R_stack_sizes) must equal len(stack_sizes)"
        assert len(R_stack_sizes) == self.nb_layers, msg
        msg = "len(A_kernel_sizes) must equal len(stack_sizes)"
        assert len(A_kernel_sizes) == self.nb_layers - 1, msg
        msg = "len(Ahat_kernel_sizes) must equal len(stack_sizes)"
        assert len(Ahat_kernel_sizes) == self.nb_layers, msg
        msg = "len(R_kernel_sizes) must equal len(stack_sizes)"
        assert len(R_kernel_sizes) == self.nb_layers, msg

        # R cells: convolutional LSTM
        R_layers = []
        for l in range(self.nb_layers):
            if l == self.nb_layers-1:
                is_last = True
                in_channels = 2*stack_sizes[l]
            else:
                is_last = False
                in_channels = 2*stack_sizes[l] + R_stack_sizes[l+1]
            out_channels = R_stack_sizes[l]
            kernel_size = R_kernel_sizes[l]
            cell = RCell(in_channels,out_channels,kernel_size,
                         LSTM_act,LSTM_c_act,
                         is_last,self.bias,use_1x1_out,FC)
            R_layers.append(cell)
        self.R_layers = nn.ModuleList(R_layers)

        # A cells: conv + ReLU + MaxPool
        A_layers = [None]
        for l in range(1,self.nb_layers): # First A layer is input
            in_channels = 2*stack_sizes[l-1]
            out_channels = stack_sizes[l]
            conv_kernel_size = A_kernel_sizes[l-1]
            cell = ACell(in_channels,out_channels,
                         conv_kernel_size,bias)
            A_layers.append(cell)
        self.A_layers = nn.ModuleList(A_layers)

        # A_hat cells: conv + ReLU
        Ahat_layers = []
        for l in range(self.nb_layers):
            in_channels = R_stack_sizes[l]
            out_channels = stack_sizes[l]
            conv_kernel_size = Ahat_kernel_sizes[l]
            if self.use_satlu and l == 0:
                # Lowest layer uses SatLU
                cell = AhatCell(in_channels,out_channels,
                                conv_kernel_size,bias,satlu_act,
                                use_satlu=True,pixel_max=pixel_max)
            else:
                cell = AhatCell(in_channels,out_channels,
                                conv_kernel_size,bias)
            Ahat_layers.append(cell)
        self.Ahat_layers = nn.ModuleList(Ahat_layers)

        # E cells: subtract, ReLU, cat
        self.E_layer = ECell(error_act) # general: same for all layers

    def forward(self,X):
        # Get initial states
        (H_tm1,C_tm1),E_tm1 = self.initialize(X)

        preds = [] # predictions for visualizing
        errors = [] # errors for computing loss

        # Loop through image sequence
        seq_len = X.shape[1]
        for t in range(seq_len):
            A_t = X[:,t,:,:,:] # X dims: (batch,len,channels,height,width)
            # Initialize list of states with consistent indexing
            R_t = [None] * self.nb_layers
            H_t = [None] * self.nb_layers
            C_t = [None] * self.nb_layers
            E_t = [None] * self.nb_layers

            # Update R units starting from the top
            for l in reversed(range(self.nb_layers)):
                R_layer = self.R_layers[l] # cell
                if l == self.nb_layers-1:
                    R_t[l],(H_t[l],C_t[l]) = R_layer(E_tm1[l],None,
                                                     (H_tm1[l],C_tm1[l]))
                else:
                    R_t[l],(H_t[l],C_t[l]) = R_layer(E_tm1[l],R_t[l+1],
                                                     (H_tm1[l],C_tm1[l]))

            # Update feedforward path starting from the bottom
            for l in range(self.nb_layers):
                # Compute Ahat
                Ahat_layer = self.Ahat_layers[l]
                Ahat_t = Ahat_layer(R_t[l])
                if l == 0 and t > 0:
                    preds.append(Ahat_t)

                # Compute E
                E_t[l] = self.E_layer(A_t,Ahat_t)

                # Compute A of next layer
                if l < self.nb_layers-1:
                    A_layer = self.A_layers[l+1]
                    A_t = A_layer(E_t[l])

            # Update
            (H_tm1,C_tm1),E_tm1 = (H_t,C_t),E_t
            if t > 0:
                errors.append(E_t) # First time step doesn't count
        # Return errors as tensor
        errors_t = torch.zeros(seq_len,self.nb_layers)
        for t in range(seq_len):
            for l in range(self.nb_layers):
                errors_t[t,l] = torch.mean(errors[t][l])
        # Return preds as tensor
        preds_t = [pred.unsqueeze(1) for pred in preds]
        preds_t = torch.cat(preds_t,dim=1) # (batch,len,in_channels,H,W)
        return preds_t, errors_t

    def initialize(self,X):
        # input dimensions
        batch_size = X.shape[0]
        height = X.shape[3]
        width = X.shape[4]
        # get dimensions of E,R for each layer
        H_0 = []
        C_0 = []
        E_0 = []
        for l in range(self.nb_layers):
            channels = self.stack_sizes[l]
            R_channels = self.R_stack_sizes[l]
            # All hidden states initialized with zeros
            Hl = torch.zeros(batch_size,R_channels,height,width).to(self.device)
            Cl = torch.zeros(batch_size,R_channels,height,width).to(self.device)
            El = torch.zeros(batch_size,2*channels,height,width).to(self.device)
            H_0.append(Hl)
            C_0.append(Cl)
            E_0.append(El)
            # Update dims
            height = int((height - 2)/2 + 1) # int performs floor
            width = int((width - 2)/2 + 1) # int performs floor
        return (H_0,C_0), E_0
