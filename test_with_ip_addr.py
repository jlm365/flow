import argparse
import os
import numpy as np
import visdom
from datetime import datetime
import matplotlib.pyplot as plt

import torch
import torch.utils.data
import torch.backends.cudnn as cudnn
from torch.autograd import Variable
from torch.utils.data.sampler import SubsetRandomSampler
from torch.utils.data import DataLoader

import flow.modules.losses as losses
import flow.datasets as datasets
import flow.modules.warps as warps
import flow.modules.estimators as estimators
from flow.utils.meter import AverageMeters
# import flow.utils.plot as plot


import sys
sys.path.append('/home/debezenac/projects/flow_icml/flow/flow/utils')
import plot

warp_names = sorted(name for name in warps.__dict__
                    if not name.startswith('__'))

parser = argparse.ArgumentParser(description='PyTorch FlowNet Training on several datasets')
parser.add_argument('--train-root', metavar='DIR', default='/net/drunk/debezenac/CMEMS_DATA/datasets/np/train',
                    help='path to training dataset')
parser.add_argument('--test-root', metavar='DIR', default='/net/drunk/debezenac/CMEMS_DATA/datasets/np/test',
                    help='path to testing dataset')
parser.add_argument('--train-zones', type=int, nargs='+', action='store', dest='train_zones', default=[20],
                    help='geographical zones to train on. To train on all zones, add range(1, 30)')
parser.add_argument('--test-zones', type=int, nargs='+', action='store', dest='test_zones', default=[20],
                    help='geographical zones to test on. To test on all zones, add range(1, 30)')
parser.add_argument('--rescale', default='norm', type=str, 
                    help='you can choose between minmax and norm')
parser.add_argument('-j', '--workers', default=4, type=int, metavar='N',
                    help='number of data loading workers (default: 4)')
parser.add_argument('-b', '--batch-size', default=1, type=int,
                    metavar='N', help='mini-batch size (default: 16)')
parser.add_argument('-s', '--split', default=.8, type=float, metavar='%',
                    help='split percentage of train samples vs test (default: .8)')
parser.add_argument('--seq-len', default=4, type=int,
                    help='number of input images as input of the estimator (horizon)')
parser.add_argument('--target-seq-len', default=6, type=int,
                    help='number of target images')
parser.add_argument('--test-target-seq-len', default=10, type=int,
                    help='number of test target images')
parser.add_argument('--weight-decay', '--wd', default=4e-4, type=float,
                    metavar='W', help='weight decay (default: 4e-4)')
parser.add_argument('--warp', default='BilinearWarpingScheme', choices=warp_names,
                    help='choose warping scheme to use:' + ' | '.join(warp_names))
parser.add_argument('--upsample', default='bilinear', choices=('deconv', 'nearest', 'bilinear'),
                    help='choose from (deconv, nearest, bilinear)')
parser.add_argument('--lr', '--learning-rate', default=0.0001, type=float,
                    metavar='LR', help='initial learning rate')
parser.add_argument('--momentum', default=0.9, type=float, metavar='M',
                    help='momentum for sgd, alpha parameter for adam')
parser.add_argument('--beta', default=0.999, type=float, metavar='M',
                    help='beta parameters for adam')
parser.add_argument('--smooth-coef', default=0.4, type=float,
                    help='coefficient associated to smoothness loss in cost function')
parser.add_argument('--div-coef', default=1, type=float,
                    help='coefficient associated to divergence loss in cost function')
parser.add_argument('--magn-coef', default=-0.003, type=float,
                    help='coefficient associated to magnitude loss in cost function')
parser.add_argument('--epochs', default=1, type=int, metavar='N',
                    help='number of total epochs to run (default: 300')
parser.add_argument('--save-every', default=10, type=int, metavar='N',
                    help='')
parser.add_argument('--save-start', default=20, type=int, metavar='N',
                    help='')
parser.add_argument('--save-root', default='/net/drunk/debezenac/data/flow_icml/saved_modules_iclr_2018', type=str, 
                    help=' ')
parser.add_argument('--env', default='main',
                    help='environnment for visdom')
parser.add_argument('--no-plot', action='store_true',
                    help='no plot images using visdom')
parser.add_argument('--no-cuda',  action='store_true',
                    help='no cuda')
parser.add_argument('--load-root', default='/net/drunk/debezenac/data/flow_icml/saved_modules_iclr_2018', type=str, 
                    help=' ')
parser.add_argument('--load-fn', default='sam.-janv.-19-04:06:42.319084_470.pt', type=str, 
                    help=' ')
parser.add_argument('--save-ims', action='store_true',
                    help='no plot images using visdom')



args = parser.parse_args()

viz = visdom.Visdom(server='http://132.227.204.175', env=args.env)

def main():
    global args, viz

    print('=> loading datasets...')
    dset = datasets.SSTSeq(args.train_root,
                           seq_len=args.seq_len,
                           target_seq_len=args.target_seq_len,
                           zones=args.train_zones,
                           rescale_method=args.rescale,
                           time_slice=slice(None, 3000),
                            normalize_uv=True,
                           )

    test_dset = datasets.SSTSeq(args.train_root,
                                seq_len=args.seq_len,
                                target_seq_len=args.test_target_seq_len,
                                zones=args.test_zones,
                                rescale_method=args.rescale,
                                time_slice=slice(3000, None),
                                normalize_uv=True,
                                )

    # train_indices = range(0, int(len(dset) * args.split))
    # val_indices = range(int(len(dset) * args.split), len(dset))

    train_loader = DataLoader(dset,
                              batch_size=args.batch_size,
                              # sampler=SubsetRandomSampler(train_indices),
                              num_workers=args.workers,
                              shuffle=True,
                              pin_memory=True
                              )
    # val_loader = DataLoader(dset,
    #                         batch_size=args.batch_size,
    #                         sampler=SubsetRandomSampler(val_indices),
    #                         num_workers=args.workers,
    #                         pin_memory=True
    #                         )
    test_loader = DataLoader(test_dset,
                             batch_size=args.batch_size,
                             shuffle=False,
                             num_workers=args.workers,
                             pin_memory=True
                             )
    print('len(test)', len(test_dset))

    splits = {
        'train': train_loader, 
        # 'valid': val_loader,
        'test': test_loader,
    }


    estimator = estimators.ConvDeconvEstimator(input_channels=args.seq_len,
                                               upsample_mode=args.upsample)
    warp = warps.__dict__[args.warp]()
    print("=> creating warping scheme '{}'".format(args.warp))

    #     to_save = {
    #         'epoch': epoch,
    #         'estimator': estimator,
    #         'warp': warp,
    #         'optim':optimizer,
    #         'err_obs': results['test']['pl'],
    #         'err_aae': results['test']['err_aae'],
    #     }
    load_path = os.path.join(args.load_root, args.load_fn)
    print(f'Loading {load_path} ...')
    loaded = torch.load(load_path)
    print('loaded', loaded)
    estimator = loaded['estimator']
    warp = loaded['warp']

    # estimator = estimator.cuda()
    # warp = warp.cuda()

    photo_loss = torch.nn.MSELoss()
    smooth_loss = losses.SmoothnessLoss(torch.nn.MSELoss())
    div_loss = losses.DivergenceLoss(torch.nn.MSELoss())
    magn_loss = losses.MagnitudeLoss(torch.nn.MSELoss())
    sim_loss = torch.nn.functional.cosine_similarity

    cudnn.benchmark = True
    optimizer = torch.optim.Adam(estimator.parameters(), args.lr,
                                 betas=(args.momentum, args.beta),
                                 weight_decay=args.weight_decay)


    _x, _ys = torch.Tensor(), torch.Tensor()

    if not args.no_cuda:
        print('=> to cuda')
        _x, _ys = _x.cuda(), _ys.cuda()
        warp.cuda(), estimator.cuda()

    viz_wins = {}
    for epoch in range(1, args.epochs + 1):

        results = {}
        for split, dl in splits.items():
            if split != 'test': continue

            meters = AverageMeters()

            if split == 'train':
                estimator.train(), warp.train()
            else:
                estimator.eval(), warp.eval()
            
            if args.save_ims:
                if args.test_target_seq_len == 10:
                    index = [8, 15, 45, 59, 63, 64, 74, 76, 83, 90, 97, 107, 111, 125, 136, 139, 155, 171, 176, 182, 204, 213, 215, 218, 223, 224, 226, 232, 260, 263]
                elif args.test_target_seq_len == 20:
                    index = [63, 83, 101, 109, 118, 135, 149, 150, 153, 158, 170, 189, 200, 229, 234]
                else:
                    print('dataset', args.test_target_seq_len)
                index = [i - args.seq_len + 1 for i in index]
                print(f'index: {index}')

            for i, (input, targets, w_targets) in enumerate(dl):

                if args.save_ims:
                    if i not in index: continue
                print('i=', i)


                with torch.no_grad():
                    _x.resize_(input.size()).copy_(input)
                    _ys.resize_(targets.size()).copy_(targets)
                    _ys = _ys.transpose(0, 1).unsqueeze(2)
                    x, ys = Variable(_x), Variable(_ys)

                    pl = 0
                    sl = 0
                    dl = 0
                    ml = 0
                    err_aee = 0

                    ims = []
                    ws = []
                    last_im = x[:, -1].unsqueeze(1)
                    for j, y in enumerate(ys):

                        w = estimator(x)
                        im = warp(x[:, -1].unsqueeze(1), w)
                        x = torch.cat([x[:, 1:], im], 1)

                        curr_pl = photo_loss(im, y)
                        pl += curr_pl
                        sl += smooth_loss(w)
                        dl += div_loss(w)
                        ml += magn_loss(w)

                        err_aee += sim_loss(w, w_targets[:, j].to('cuda')).mean()
                        # print(w_targets[:, j].shape, w.shape, 'ok')

                        ims.append(im.cpu().data.numpy())
                        ws.append(w.cpu().data.numpy())

                    pl /= args.test_target_seq_len
                    sl /= args.test_target_seq_len
                    dl /= args.test_target_seq_len
                    ml /= args.test_target_seq_len
                    err_aee /= args.test_target_seq_len
                    # print('err', err_aee)

                    loss = pl + args.smooth_coef * sl + args.div_coef * dl + args.magn_coef * ml

                    # if split == 'train':
                    #     optimizer.zero_grad()
                    #     loss.backward()
                    #     optimizer.step()

                    meters.update(
                        dict(loss=loss.item(),
                             pl=pl.item(),
                             dl=dl.item(),
                             sl=sl.item(),
                             ml=ml.item(),
                             err_aae=err_aee.item(),
                             # err_unobs=.item(),
                             # err_obs
                             ),
                        n=x.size(0)
                    )
                    images = [
                        # ('target', {
                        #     'in': input.transpose(0, 1).numpy(),
                        #     'out': ys.cpu().data.numpy()
                        #     }
                        # ),
                        ('im', {
                            'out': ims
                            }
                        ),
                        ('ws', {
                            'out': ws
                            }
                        ),
                        # ('ws_target', {
                        #     'out': w_targets.transpose(0, 1).numpy()
                        #     }
                        # ),
                    ]
                    if args.save_ims:
                        import matplotlib.pyplot as plt
                        images_save_root = '/net/drunk/debezenac/data/flow_icml/saved_images/png/sst/test/adv/' + str(args.test_target_seq_len)
                        # images_save_root = '/net/drunk/debezenac/data/flow_icml/saved_images/png/sst/adv/20'
                        nplots = 1
                        images_save_fn = os.path.join(images_save_root, f'images_{load_path.replace("/", "_")}_{i + args.seq_len - 1}.png')
                        pl = plot.plot_results(images, min(nplots, args.batch_size), cmap='tarn', renorm=False)
                        print(images_save_fn)
                        plt.savefig(images_save_fn)

            if not args.no_plot:
                images = [
                    ('target', {
                        'in': input.transpose(0, 1).numpy(),
                        'out': ys.cpu().data.numpy()
                        }
                    ),
                    ('im', {
                        'out': ims
                        }
                    ),
                    ('ws', {
                        'out': ws
                        }
                    ),
                    ('ws_target', {
                        'out': w_targets.transpose(0, 1).numpy()
                        }
                    ),
                ]

                plt = plot.from_matplotlib(plot.plot_results(images))
                viz.image(plt.transpose(2, 0, 1),
                          opts=dict(title='{}, epoch {}'.format(split.upper(), epoch)),
                          win=list(splits).index(split),
                          )

            results[split] = meters.avgs()
            print('\n\nEpoch: {} {}: {}\t'.format(epoch, split, meters))
            print('seq_len:', args.test_target_seq_len)

        # transposing the results dict
        res = {}
        legend = []
        for split in results:
            legend.append(split)
            for metric, avg in results[split].items():
                res.setdefault(metric, [])
                res[metric].append(avg)
        # plotting
        for metric in res:
            y = np.expand_dims(np.array(res[metric]), 0)
            x = np.array([[epoch]*len(results)])
            if epoch == 1:
                win = viz.line(X=x, Y=y,
                               opts=dict(showlegend=True,
                                    legend=legend,
                                    title=metric))
                viz_wins[metric] = win
            else:
                viz.line(X=x, Y=y,
                         opts=dict(showlegend=True,
                            legend=legend,
                            title=metric),
                         win=viz_wins[metric],
                         update='append')

        # if (epoch % args.save_every == 0) and (epoch >= args.save_start):
        #     to_save = {
        #         'epoch': epoch,
        #         'estimator': estimator,
        #         'warp': warp,
        #         'optim':optimizer,
        #         'err_obs': results['test']['pl'],
        #         'err_aae': results['test']['err_aae'],
        #     }
        #     time_str = datetime.now().strftime("%a-%b-%d-%H:%M:%S.%f")
        #     save_path = os.path.join(args.save_root, f'{time_str}_{epoch}.pt')
        #     print(f'Saving modules to {save_path} ...')
        #     torch.save(to_save, save_path)

if __name__ == '__main__':
    main()
