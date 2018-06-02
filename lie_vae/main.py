import os.path
from pprint import pprint
import argparse
from math import pi
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from tensorboardX import SummaryWriter
import numpy as np

from lie_vae.datasets import SelectedDataset, ObjectsDataset, ThreeObjectsDataset, \
    HumanoidDataset, ColorHumanoidDataset, SingleChairDataset, SphereCubeDataset, \
    ToyDataset
from lie_vae.experiments import UnsupervisedExperiment, SemiSupervisedExperiment
from lie_vae.vae import ChairsVAE
from lie_vae.utils import random_split, LinearSchedule
from lie_vae.beta_schedule import get_beta_schedule

device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')


def main():
    args = parse_args()
    pprint(vars(args))
    if args.name is not None:
        args.log_dir = 'runs/'+args.name
        args.save_dir = 'outputs/'+args.name

    log = SummaryWriter(args.log_dir)

    item_rep = None  # Possibly given fixed harmonics
    if args.dataset == 'objects':
        dataset = ObjectsDataset()
    elif args.dataset == 'objects3':
        dataset = ThreeObjectsDataset()
    elif args.dataset == 'chairs':
        dataset = SelectedDataset()
    elif args.dataset == 'humanoid':
        dataset = HumanoidDataset(subsample=args.subsample)
    elif args.dataset == 'chumanoid':
        dataset = ColorHumanoidDataset(subsample=args.subsample)
    elif args.dataset == 'single':
        dataset = SingleChairDataset(subsample=args.subsample)
    elif args.dataset == 'spherecube':
        dataset = SphereCubeDataset(subsample=args.subsample)
    elif args.dataset == 'toy':
        dataset = ToyDataset()
        if args.fixed_spectrum:
            item_rep = dataset[0][1]
    else:
        raise RuntimeError('Wrong dataset')
    if len(dataset) == 0:  #pylint: disable=C1801
        raise RuntimeError('Dataset empty')

    mlp_activation = {
        'relu': nn.ReLU,
        'softplus': nn.Softplus,
        'tanh': nn.Tanh
    }[args.mlp_activation]

    model = ChairsVAE(
        content_dims=args.content_dims,
        latent_mode=args.latent_mode,
        mean_mode=args.mean_mode,
        decoder_mode=args.decoder_mode,
        encode_mode=('toy' if args.dataset == 'toy' else 'conv'),
        deconv_mode=('toy' if args.dataset == 'toy' else args.deconv_mode),
        rep_copies=args.rep_copies,
        degrees=args.degrees,
        deconv_hidden=args.deconv_hidden,
        batch_norm=args.batch_norm,
        rgb=dataset.rgb,
        single_id=dataset.single_id,
        normal_dims=args.normal_dims,
        deterministic=args.deterministic,
        item_rep=item_rep,
        wigner_transpose=args.wigner_transpose,
        mlp_layers=args.mlp_layers,
        mlp_hidden=args.mlp_hidden,
        mlp_activation=mlp_activation
    ).to(device)

    if args.continue_epoch > 0:
        print('Loading..')
        model.load_state_dict(torch.load(os.path.join(
            args.save_dir, 'model.pickle')))

    num_valid = 25000
    num_test = 25000

    split = [num_valid, num_test, len(dataset) - num_valid - num_test]
    valid_dataset, test_dataset, train_dataset = random_split(dataset, split)

    print('Datset splits: train={}, valid={}, test={}'.format(
        len(train_dataset), len(valid_dataset), len(test_dataset)))

    optimizer = torch.optim.Adam(model.parameters())

    if args.experiment == 'unsupervised':
        exp_cls = UnsupervisedExperiment
        exp_kwargs = {}
    elif args.experiment == 'semi':
        exp_cls = SemiSupervisedExperiment
        exp_kwargs = {
            'num_labelled': args.semi_labelled,
            'lambda_supervised': args.semi_lambda}
    else:
        raise RuntimeError('Wrong experiment')

    if args.equivariance is not None:
        equivariance = LinearSchedule(0, args.equivariance, 1000, args.equivariance_end_it)
    else:
        equivariance = None

    experiment = exp_cls(
        log=log,
        model=model,
        optimizer=optimizer,
        beta_schedule=get_beta_schedule(args.beta_schedule, args.beta),
        train_dataset=train_dataset,
        test_dataset=valid_dataset,
        elbo_samples=args.elbo_samples,
        report_freq=args.report_freq,
        clip_grads=args.clip_grads,
        selective_clip=args.selective_clip,
        equivariance_lamb=equivariance,
        continuity_lamb=args.continuity,
        continuity_scale=2*pi/args.continuity_iscale,
        **exp_kwargs
    )

    early_stop_counter = 0
    for epoch in range(args.continue_epoch, args.epochs):
        previous_best_value = experiment.best_value
        experiment.train(epoch)

        if args.save_dir:
            if args.max_early_stop is None or previous_best_value != experiment.best_value:
                if not os.path.exists(args.save_dir):
                    os.makedirs(args.save_dir)
                torch.save(model.state_dict(), os.path.join(
                    args.save_dir, 'model.pickle'))
            elif args.max_early_stop is not None and early_stop_counter < args.max_early_stop:
                early_stop_counter += 1
            else:
                break
    log.close()

    if not args.beta == 0:
        print('Computing LL..')
        model = model.eval()
        test_dataset = DataLoader(test_dataset, batch_size=4, shuffle=True, num_workers=5)
        ll = np.mean([model.log_likelihood(batch[-1].to(device), n=500).data.cpu().numpy()
                      for batch in test_dataset])
        print('LL: {:.2f}'.format(ll))
        with open('ll.txt', 'a') as f:
            f.write("{} : {:4f}\n".format(args.name, ll))


def parse_args():
    parser = argparse.ArgumentParser('VAE experiment')
    parser.add_argument('--dataset', default='chairs',
                        help='Data set to use, [chairs, objects, objects3,'
                             'spherecube, chumanoid, single]')
    parser.add_argument('--decoder_mode', default='action',
                        help='[action, mlp]')
    parser.add_argument('--latent_mode', default='so3',
                        help='[so3, so3f, normal]')
    parser.add_argument('--mean_mode', default='alg', help='For SO(3). Choose [q, alg, s2s2, s2s1]')
    parser.add_argument('--experiment', default='unsupervised',
                        help='[unsupervised, semi]')
    parser.add_argument('--deconv_mode', default='deconv',
                        help='Deconv mode [deconv, upsample]')
    parser.add_argument('--batch_norm', type=int, default=1,
                        help='Whether to use Batch Norm in conv')
    parser.add_argument('--beta', type=float, default=1.)
    parser.add_argument('--beta_schedule', type=str)
    parser.add_argument('--epochs', type=int, default=100)
    parser.add_argument('--report_freq', type=int, default=1250)
    parser.add_argument('--degrees', type=int, default=6)
    parser.add_argument('--deconv_hidden', type=int, default=200)
    parser.add_argument('--content_dims', type=int, default=10,
                        help='The dims of the content latent code')
    parser.add_argument('--rep_copies', type=int, default=10,
                        help='The dims of the virtual signal on the Sphere, '
                             'i.e. the number of copies of the representation.')
    parser.add_argument('--clip_grads', type=float, default=1E-5)
    parser.add_argument('--selective_clip', action='store_true')
    parser.add_argument('--elbo_samples', type=int, default=1)
    parser.add_argument('--log_dir')
    parser.add_argument('--save_dir')
    parser.add_argument('--name')
    parser.add_argument('--continue_epoch', type=int, default=0)
    parser.add_argument('--semi_labelled', type=int, default=100,
                        help='Number of labelled samples')
    parser.add_argument('--semi_lambda', type=float, default=1.,
                        help='Relative strength of supervised loss')
    parser.add_argument('--semi_batch', type=int, default=1,
                        help='Number of labelled samples in each batch')
    parser.add_argument('--continuity', type=float,
                        help='Strength of continuity loss')
    parser.add_argument('--continuity_iscale', type=float, default=200,
                        help='Inverse algebra distance with which continuity'
                             'is measured. Distance is 2pi/iscale.')
    parser.add_argument('--equivariance', type=float,
                        help='Strength of equivariance loss')
    parser.add_argument('--equivariance_end_it', type=int, default=20000,
                        help='It at which equivariance max')
    parser.add_argument('--max_early_stop', type=int, default=50,
                        help='How many epochs to train without improvements'
                        'before doing early stopping.')
    parser.add_argument('--subsample', type=float, default=1.,
                        help='Part of the dataset to subsample in [0,1].')
    parser.add_argument('--normal_dims', type=int, default=3,
                        help='Latent space dims for Normal')
    parser.add_argument('--deterministic', action='store_true',
                        help='Let reparametrizers return means.')
    parser.add_argument('--wigner_transpose', action='store_true',
                        help='Take tranposed wigner matrices')
    parser.add_argument('--fixed_spectrum', action='store_true',
                        help='For Toy experiment, use ground truth specturm')
    parser.add_argument('--mlp_hidden', type=int, default=50,
                        help='Hidden dims of MLP decoder')
    parser.add_argument('--mlp_layers', type=int, default=3,
                        help='Layers of MLP decoder')
    parser.add_argument('--mlp_activation', default='relu',
                        help='Activation of MLP decoder')

    return parser.parse_args()


if __name__ == '__main__':
    main()
