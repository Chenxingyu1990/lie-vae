import re

import numpy as np
import os.path
from glob import glob

import torch
from PIL import Image
from torch.utils.data import Dataset, TensorDataset

from lie_learn.groups.SO3 import change_coordinates as SO3_coordinates


class ShapeDataset(Dataset):
    rgb = False
    single_id = False

    def __init__(self, directory):
        self.directory = directory
        index_path = os.path.join(directory, 'files.txt')
        if os.path.exists(index_path):
            with open(index_path, 'r') as f:
                self.files = f.read().splitlines()
            self.root = directory
        else:
            self.files = glob(os.path.join(directory, '**/*.jpg'), recursive=True)
            self.root = None

    def __len__(self):
        return len(self.files)

    def __getitem__(self, idx):
        filename = self.files[idx]
        path = os.path.join(self.root, filename) if self.root else filename
        image = Image.open(path)
        image_tensor = torch.tensor(np.array(image), dtype=torch.float32) / 255
        quaternion = self.filename_to_quaternion(filename)

        if not self.rgb:
            if image_tensor.dim() == 3:  # Mean if RGB
                image_tensor = image_tensor.mean(-1)

            image_tensor = image_tensor.unsqueeze(0)  # Add color channel
        else:
            image_tensor = image_tensor.permute(2, 0, 1)

        group_el = torch.tensor(SO3_coordinates(quaternion, 'Q', 'MAT'),
                                dtype=torch.float32)
        name = self.filename_to_name(filename)
        return name, group_el, image_tensor

    def filename_to_quaternion(self, filename):
        """Remove extension, then retrieve _ separated floats"""
        matches = re.findall(r'-?[01]\.[0-9]{4}', filename)
        assert len(matches) == 4, 'No quaternion found in '+filename
        return [float(x) for x in matches]

    def filename_to_name(self, filename):
        match = re.search(r'([A-z0-9]+)\.obj', filename)

        assert match is not None, 'Could not find object id from filename'

        return match.group(1)


class NamedDataset(ShapeDataset):
    data_path, names_path = None, None

    def __init__(self):
        super().__init__(self.data_path)
        with open(self.names_path, 'r') as f:
            self.name = re.findall('([A-z0-9]+)\.obj', f.read())
        self.map = {n: i for i, n in enumerate(self.name)}

    def __getitem__(self, idx):
        name, group_el, image_tensor = super().__getitem__(idx)
        return self.map[name], group_el, image_tensor


class SelectedDataset(NamedDataset):
    data_path = 'data/chairs/ten'
    names_path = 'data/chairs/selected_chairs.txt'


class ObjectsDataset(NamedDataset):
    data_path = 'data/objects'
    names_path = 'data/objects/objects.txt'


class ThreeObjectsDataset(NamedDataset):
    data_path = 'data/objects3'
    names_path = 'data/objects3/objects.txt'


class HumanoidDataset(ShapeDataset):
    def __init__(self):
        super().__init__('data/humanoid')

    def filename_to_name(self, filename):
        return 0


class ColorHumanoidDataset(ShapeDataset):
    rgb = True

    def __init__(self):
        super().__init__('data/chumanoid')

    def filename_to_name(self, filename):
        return 0


class SingleChairDataset(ShapeDataset):
    single_id = True

    def __init__(self):
        super().__init__('data/chairs/single')

    def filename_to_name(self, filename):
        return 0


class CubeDataset(TensorDataset):
    def __init__(self, mode):
        assert mode in ['train', 'test', 'dev'], "Mode should be train|test|dev"

        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '../data/cubes')
        data = np.load(os.path.join(data_dir, mode+'_data2.npy'))
        # labels = np.load(os.path.join(data_dir, mode+'_labels.npy'))
        super().__init__(torch.from_numpy(data.astype(np.float32))) #, torch.from_numpy(labels))