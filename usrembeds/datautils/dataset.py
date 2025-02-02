import json
import os
import torch
import pandas as pd
import librosa
import numpy as np
import scipy.signal as sps


from torch.utils.data import Dataset
from tqdm import tqdm
from random import randint

import concurrent.futures  # Added for multi-threading


class ContrDatasetMERT(Dataset):
    # static embeddings variable to store the embeddings
    embeddings = {}

    def __init__(
        self,
        embs_dir,
        stats_path,
        split,
        usrs,
        nneg=10,
        multiplier=10,
        transform=None,
        preload=False,  # New parameter for preloading
        max_workers=12,  # Number of threads for preloading
    ):
        self.embs_dir = embs_dir
        self.stats_path = stats_path
        self.nneg = nneg
        self.multiplier = multiplier
        self.transform = transform
        self.preload = preload  # Store the preload flag
        self.max_workers = max_workers  # Number of threads

        print("[DATASET] Creating dataset")

        # Set embedding keys from the split
        self.emb_keys = split

        # Load the stats
        self.stats = pd.read_csv(stats_path)
        self.stats["count"] = self.stats["count"].astype(int)

        # Remove entries with no embeddings
        self.stats = self.stats[self.stats["id"].isin(self.emb_keys)].reset_index(
            drop=True
        )

        # Remove users not in the split
        self.stats = self.stats[self.stats["userid"].isin(usrs)]

        self.idx2usr = self.stats["userid"].unique().tolist()

        # Compute user stats
        self.usersums = self.stats.groupby("userid")["count"].sum()
        self.userstd = self.stats.groupby("userid")["count"].std()
        self.usercount = self.stats.groupby("userid")["count"].count()

        self.user2songs = (
            self.stats.groupby("userid")
            .apply(lambda x: list(zip(x["id"], x["count"])))
            .to_dict()
        )

        # Number of users
        self.nusers = self.stats["userid"].nunique()

        # Preload embeddings into memory if preload=True
        if self.preload:
            print("[DATASET] Preloading embeddings into RAM using multi-threading")
            self._preload_embeddings()

    def _load_embedding(self, key):
        """
        Helper function to load a single embedding JSON file.
        Returns a tuple of (key, embedding) or (key, None) if not found.
        """
        if key in ContrDatasetMERT.embeddings:
            return None  # Skip if already loaded
        emb_file = os.path.join(self.embs_dir, f"{key}.json")
        if os.path.isfile(emb_file):
            try:
                with open(emb_file, "r") as f:
                    data = json.load(f)
                    if key in data:
                        return key, data[key][0]
                    else:
                        print(f"[WARNING] Key '{key}' not found in {emb_file}")
                        return key, None
            except json.JSONDecodeError:
                print(f"[ERROR] Failed to decode JSON from {emb_file}")
                return key, None
        else:
            print(f"[WARNING] Embedding file '{emb_file}' does not exist")
            return key, None

    def _preload_embeddings(self):
        """
        Preloads all embeddings into the self.embeddings dictionary using multi-threading.
        """
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers
        ) as executor:
            # Use list to eagerly evaluate and use tqdm for progress bar
            results = list(
                tqdm(
                    executor.map(self._load_embedding, self.emb_keys),
                    total=len(self.emb_keys),
                    desc="Preloading embeddings",
                )
            )

        # Populate the embeddings dictionary
        for key, emb in results:
            if emb is not None:
                ContrDatasetMERT.embeddings[key] = emb
        print(
            f"[DATASET] Preloaded {len(ContrDatasetMERT.embeddings)} embeddings out of {len(self.emb_keys)}"
        )

    def __len__(self):
        return self.nusers * self.multiplier

    def __getitem__(self, idx):

        idx = idx % self.nusers

        usr = self.idx2usr[idx]

        pos = self.user2songs[usr]

        neg = list(set(self.emb_keys) - set([song for song, _ in pos]))

        # Take random positive sample
        pos_sample = pos[randint(0, len(pos) - 1)]
        posset, count = pos_sample

        # Compute pos sample weight
        mean = self.usersums[usr] / self.usercount[usr]
        top70 = mean + self.userstd[usr]
        weight = min(1, count / top70)

        # Take random negative samples
        negset = np.random.choice(neg, size=self.nneg, replace=False)

        poslist = []

        if self.preload:
            # Use preloaded embeddings
            poslist = [ContrDatasetMERT.embeddings[posset]]
        else:
            # Load the embeddings from disk
            emb_file = os.path.join(self.embs_dir, f"{posset}.json")
            if os.path.isfile(emb_file):
                try:
                    with open(emb_file, "r") as f:
                        data = json.load(f)
                        if posset in data:
                            poslist = [data[posset][0]]
                        else:
                            print(f"[WARNING] Key '{posset}' not found in {emb_file}")
                            poslist = [[0.0]]  # Placeholder
                except json.JSONDecodeError:
                    print(f"[ERROR] Failed to decode JSON from {emb_file}")
                    poslist = [[0.0]]  # Placeholder
            else:
                print(f"[WARNING] Embedding file '{emb_file}' does not exist")
                poslist = [[0.0]]  # Placeholder

        neglist = []
        for neg in negset:
            if self.preload:
                neg_emb = ContrDatasetMERT.embeddings[neg]
                neglist.append(neg_emb)
            else:
                emb_file = os.path.join(self.embs_dir, f"{neg}.json")
                if os.path.isfile(emb_file):
                    try:
                        with open(emb_file, "r") as f:
                            data = json.load(f)
                            if neg in data:
                                neg_emb = data[neg][0]
                            else:
                                print(f"[WARNING] Key '{neg}' not found in {emb_file}")
                                neg_emb = [0.0]  # Placeholder
                    except json.JSONDecodeError:
                        print(f"[ERROR] Failed to decode JSON from {emb_file}")
                        neg_emb = [0.0]  # Placeholder
                else:
                    print(f"[WARNING] Embedding file '{emb_file}' does not exist")
                    neg_emb = [0.0]  # Placeholder
                neglist.append(neg_emb)

        posemb = torch.Tensor(poslist)
        negemb = torch.Tensor(neglist)

        return idx, posemb, negemb, weight


class ContrDatasetOL3(Dataset):
    def __init__(
        self,
        embs_dir,
        stats_path,
        nneg=10,
        multiplier=10,
        transform=None,
    ):
        self.embs_dir = embs_dir
        self.stats_path = stats_path
        self.nneg = nneg
        self.multiplier = multiplier
        self.transform = transform

        print("[DATASET] Loading files and keys")
        embedding_files = [
            f for f in os.listdir(embs_dir) if os.path.isfile(os.path.join(embs_dir, f))
        ]
        embedding_files.remove("allkeys.json")

        with open(os.path.join(embs_dir, "allkeys.json"), "r") as f:
            self.allkeys = json.load(f)

        self.allkeys.remove("metadata")

        # del self.allkeys["metadata"]
        # mapping the keys to a list because dict lookup is just too slow
        self.emb_map = {key: idx for idx, key in enumerate(self.allkeys)}

        self.emb_list = [[] for _ in self.allkeys]

        print("[DATASET] Loading embeddings")
        for num, file in enumerate(tqdm(embedding_files)):
            with open(os.path.join(embs_dir, file), "r") as f:
                data = json.load(f)
                for key, value in data.items():
                    if key != "metadata":
                        # print(len(value[0]))
                        # if len(value[0]) != 512:
                        #     breakpoint()
                        self.emb_list[self.emb_map[key]].extend(value)

        print("[DATASET] Loading users stats")
        # load the stats
        self.stats = pd.read_csv(stats_path)
        self.stats["count"] = self.stats["count"].astype(int)

        # remove tracks with no embeddings
        self.stats = self.stats[self.stats["id"].isin(self.allkeys)].reset_index(
            drop=True
        )

        self.idx2usr = self.stats["userid"].unique().tolist()

        # Group by 'userid' and aggregate 'id' into a list
        # self.user2songs = self.stats.groupby("userid")["id"].apply(list).to_dict()

        # breakpoint()

        self.usersums = self.stats.groupby("userid")["count"].sum()
        self.userstd = self.stats.groupby("userid")["count"].std()
        self.usercount = self.stats.groupby("userid")["count"].count()

        self.user2songs = (
            self.stats.groupby("userid")
            .apply(lambda x: list(zip(x["id"], x["count"])))
            .to_dict()
        )
        # breakpoint()
        # number of users
        self.nusers = self.stats["userid"].nunique()

        # breakpoint()

    def __len__(self):
        return self.nusers * self.multiplier

    def __getitem__(self, idx):

        idx = idx % self.nusers
        # TODO: implement way to use playcount to select positive samples
        usr = self.idx2usr[idx]
        # breakpoint()
        pos = self.user2songs[usr]
        # count = torch.Tensor(count).type(torch.int32)
        neg = list(set(self.allkeys) - set(pos))

        pos_sample = pos[randint(0, len(pos) - 1)]
        posset, count = pos_sample

        mean = self.usersums[usr] / self.usercount[usr]
        top70 = mean + self.userstd[usr]
        weight = min(1, count / top70)
        # weight = torch.Tensor([weight])
        # breakpoint()

        negset = np.random.choice(neg, size=self.nneg, replace=False)

        poslist = []
        # for pos in posset:
        #     embs = self.emb_list[self.emb_map[pos]]
        #     # if len(embs) == 0:
        #     #     print(f"Empty embedding for {pos}")
        #     #     breakpoint()
        #     poslist.append(embs[randint(0, len(embs) - 1)])

        embs = self.emb_list[self.emb_map[posset]]
        poslist.append(embs[randint(0, len(embs) - 1)])

        neglist = []
        for neg in negset:
            embs = self.emb_list[self.emb_map[neg]]
            # if len(embs) == 0:
            #     print(f"Empty embedding for {neg}")
            #     breakpoint()
            neglist.append(embs[randint(0, len(embs) - 1)])

        # neglist = [
        #     self.emb_list[self.emb_map[neg]][
        #         randint(0, len(self.emb_list[self.emb_map[neg]]))
        #     ]
        #     for neg in negset
        # ]

        posemb = torch.Tensor(poslist)
        negemb = torch.Tensor(neglist)

        # print(negemb.shape)
        # breakpoint()
        return idx, posemb, negemb, weight


class MusicDataset(Dataset):
    def __init__(
        self,
        data_dir,
        type="audio",
        audio_len=1,
        resample=None,
        nsamples=None,
        repeat=1,
        transform=None,
    ):
        self.data_dir = data_dir
        self.type = type
        self.audio_len = audio_len
        self.resample = resample
        self.repeat = repeat

        self.transform = transform

        # load the songs
        self.tracks_paths = [
            os.path.join(data_dir, track)
            for track in os.listdir(data_dir)
            if track.endswith(".mp3")
        ]

        if nsamples is not None:
            self.tracks_paths = self.tracks_paths[:nsamples]

    def __len__(self):
        return len(self.tracks_paths) * self.repeat

    def __getitem__(self, idx):

        idx = idx % len(self.tracks_paths)

        stat = {}
        track_path = self.tracks_paths[idx]
        stat["id"] = track_path.split("/")[-1].split("_")[0]

        # Convert mp3 to wav
        y, sr = librosa.load(track_path, sr=None)
        stat["sr"] = sr
        if self.resample is not None:
            # Resample data
            number_of_samples = round(len(y) * float(self.resample) / sr)
            y = sps.resample(y, number_of_samples)
            stat["sr"] = self.resample

        # if audio is too short, repeat it
        pad_times = int((sr * self.audio_len) / len(y))
        y = np.tile(y, pad_times + 1)

        # take random audio_len sec long snippet
        snip_len = min(len(y), sr * self.audio_len) - 1
        snip_idx = np.random.randint(0, len(y) - snip_len)
        snip = y[snip_idx : snip_idx + snip_len]

        return stat, snip


def get_dataloaders(
    embs_path,
    stats_path,
    splits_path,
    nneg,
    mul,
    batch_size,
    workers=8,
):

    with open(splits_path, "r") as f:
        splits = json.load(f)

    train = splits["train"]
    test = splits["test"]
    users = splits["users"]

    train_dataset = ContrDatasetMERT(
        embs_path,
        stats_path,
        split=train,
        usrs=users,
        nneg=nneg,
        multiplier=mul,
    )

    test_dataset = ContrDatasetMERT(
        embs_path,
        stats_path,
        split=test,
        usrs=users,
        nneg=nneg,
        multiplier=mul,
    )

    train_loader = torch.utils.data.DataLoader(
        train_dataset,
        batch_size=batch_size,
        shuffle=True,
        num_workers=workers,
    )

    test_loader = torch.utils.data.DataLoader(
        test_dataset,
        batch_size=batch_size,
        shuffle=True,
        num_workers=workers,
    )

    return train_loader, test_loader, len(users)


if __name__ == "__main__":

    music_path = "../scraper/music"
    membs_path = "usrembeds/data/embeddings/embeddings_full_split"
    stats_path = "usrembeds/data/clean_stats.csv"
    splits_path = "usrembeds/data/splits.json"

    with open(splits_path, "r") as f:
        splits = json.load(f)

    train = splits["train"]
    test = splits["test"]
    users = splits["users"]

    train_dataset = ContrDatasetMERT(
        membs_path,
        stats_path,
        split=train,
        usrs=users,
    )

    test_dataset = ContrDatasetMERT(
        membs_path,
        stats_path,
        split=test,
        usrs=users,
    )

    train_loader = torch.utils.data.DataLoader(
        train_dataset,
        batch_size=8,
        shuffle=True,
        num_workers=8,
    )

    test_loader = torch.utils.data.DataLoader(
        test_dataset,
        batch_size=8,
        shuffle=True,
        num_workers=8,
    )

    breakpoint()
    for track in tqdm(train_loader):
        idx, posemb, negemb, weights = track
        # breakpoint()
