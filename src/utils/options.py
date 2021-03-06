import argparse
import multiprocessing

class Options(object):
    """
    Options class - defines all train/test options and prints them out as a summary.
    Inspired by
    https://github.com/junyanz/pytorch-CycleGAN-and-pix2pix/blob/master/options/base_options.py
    """
    def __init__(self):
        parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        # basic options
        parser.add_argument('--data_dir', required=True, help='path to directory where the dataset is stored, should have subfolders trainA, trainB, testA, testB')
        parser.add_argument('--save_dir', required=True, help='checkpoints and Tensorboard summaries are saved here')
        parser.add_argument('--gpu_id', type=int, default=0, help='gpu id to run model on, use -1 for CPU, multigpu not supported')
        # model options
        parser.add_argument('--ngf', type=int, default=64, help='number of gen filters in the first conv layer')
        parser.add_argument('--ndf', type=int, default=64, help='number of disc filters in the first conv layer')
        parser.add_argument('--instance_norm', action='store_false', help='if true, uses instance normalisation after each conv layer in D and G')
        parser.add_argument('--init_scale', type=float, default=0.02, help='stddev for weight initialisation; small variance helps prevent colour inversion.')
        parser.add_argument('--gen_skip', action='store_true', help='if true, use skip connection from first residual block to last in generator')
        parser.add_argument('--resize_conv', action='store_false', help='if true, replace conv2dtranspose in generator with upsample -> conv2d')
        parser.add_argument('--use_dropout', action='store_true', help='if true, use dropout for the generator')
        parser.add_argument('--dropout_prob', type=float, default=0.5, help='dropout probability for all layers in generator')
        # dataset options
        cpu_count = multiprocessing.cpu_count()
        parser.add_argument('--num_threads', type=int, default=cpu_count, help='number of CPU threads to use for loading data')
        parser.add_argument('--img_size', type=int, default=256, help='input image size')
        self.parser = parser

    def parse(self, training):
        # get training/testing options
        if training:
             self.parser = self._get_train_options(self.parser)
        else:
             self.parser = self._get_test_options(self.parser)

        opt = self.parser.parse_args()
        self.print_options(opt)
        return opt

    def print_options(self, opt):
        message = ''
        message += '----------------- Options ---------------\n'
        for option, value in sorted(vars(opt).items()):
            comment = ''
            default = self.parser.get_default(option)
            if value != default:
                comment = '\t[default: %s]' % str(default)
            message += '{:>25}: {:<30}{}\n'.format(str(option), str(value), comment)
        message += '----------------- End -------------------'
        print(message)

    def _get_train_options(self, parser):
        # training specific options
        parser.add_argument('--training', action='store_false', help='boolean for training/testing')
        parser.add_argument('--load_checkpoint', action='store_false', help='if true, loads latest checkpoint')
        parser.add_argument('--save_epoch_freq', type=int, default=5, help='frequency of saving checkpoints at the end of epochs')
        parser.add_argument('--save_summaries', action='store_false', help='if true, stores tensorboard summaries. Turn off to speed up training significantly')
        parser.add_argument('--summary_freq', type=int, default=100, help='frequency of saving saving tensorboard summaries in training steps')
        parser.add_argument('--epochs', type=int, default=200, help='number of epochs to train the model; learning rate decays to 0 by epoch 200')
        parser.add_argument('--batch_size', type=int, default=1, help='input batch size')
        parser.add_argument('--lr', type=float, default=0.0002, help='initial learning rate for adam')
        parser.add_argument('--beta1', type=float, default=0.5, help='momentum term for adam')
        parser.add_argument('--niter', type=int, default=100, help='number of epochs at initial learning rate')
        parser.add_argument('--niter_decay', type=int, default=100, help='number of epochs to linearly decay learning rate to zero')
        parser.add_argument('--gan_mode', type=str, default='lsgan', help='GAN loss type [vanilla | lsgan | wgangp | rgan]. vanilla is the cross-entropy loss from original paper')
        parser.add_argument('--buffer_size', type=int, default=50, help='size of the image history buffer')
        parser.add_argument('--cyc_lambda', type=float, default=10, help='weight for cycle-consistency loss')
        parser.add_argument('--identity_lambda', type=float, default=0.5, help='weight for identity loss: idt_loss * cyc_lambda * identity_lambda; set to 0 to disable.')
        return parser

    def _get_test_options(self, parser):
        # test specific options
        parser.add_argument('--training', action='store_true', help='boolean for training/testing')
        parser.add_argument('--results_dir', required=True, help='directory to save result images')
        parser.add_argument('--num_test', type=int, default=50, help='number of test images to generate')
        return parser
