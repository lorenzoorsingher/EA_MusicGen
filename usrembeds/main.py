import torchopenl3
import torch
import numpy as np

from datautils.dataset import MusicDataset, StatsDataset, ContrDataset
from models.model import Aligner
from utils import plot_music_batch
import torch.nn as nn

from torch import optim

from tqdm import tqdm

from dotenv import load_dotenv
import os
import wandb
import datetime
import argparse

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"  # use GPU if we can!


def contrastive_loss(out, possim, negsim, temp=0.07):
    # breakpoint()
    cos = nn.CosineSimilarity(dim=2, eps=1e-6)

    possim = cos(out, posemb)

    out = out.repeat(1, negemb.shape[1], 1)
    negsim = cos(out, negemb)

    logits = torch.cat((possim, negsim), dim=1) / temp
    exp = torch.exp(logits)
    loss = -torch.log(exp[:, 0] / torch.sum(exp, dim=1))
    loss = torch.mean(loss)
    return loss


def eval_loop(model, val_loader):

    model.eval()

    losses = []

    correct = 0
    total = 0
    for tracks in tqdm(val_loader):

        # [B]
        # [B, 1, EMB]
        # [B, NNEG, EMB]
        idx, posemb, negemb = tracks

        idx = idx.to(DEVICE)
        posemb = posemb.to(DEVICE)
        negemb = negemb.to(DEVICE)

        ellemb = torch.cat((posemb, negemb), dim=1)

        urs_x, embs = model(idx, ellemb)

        # breakpoint()
        posemb_out = embs[
            :,
            0,
        ].unsqueeze(dim=1)
        negemb_out = embs[
            :,
            1:,
        ]

        # breakpoint()
        out = urs_x.unsqueeze(1)
        # breakpoint()

        # breakpoint()
        cos = nn.CosineSimilarity(dim=2, eps=1e-6)

        possim = cos(out, posemb_out).squeeze(1)

        out = out.repeat(1, negemb_out.shape[1], 1)
        negsim = cos(out, negemb_out)

        negsim = negsim.view(-1, negemb_out.shape[1])

        mean_negsim = torch.mean(negsim, dim=1)

        correct += (possim > mean_negsim).sum().item()
        total += possim.shape[0]
        # breakpoint()
        # logits = torch.cat((possim, negsim), dim=1) / 0.07
        # exp = torch.exp(logits)
        # loss = -torch.log(exp[:, 0] / torch.sum(exp, dim=1))
        # loss = torch.mean(loss)

        # losses.append(loss.item())

        # print(np.mean(losses))
    model.train()
    return correct / total


def get_args():
    """
    Function to get the arguments from the command line

    Returns:
    - args (dict): arguments
    """
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="""Get the params""",
    )

    parser.add_argument(
        "-E",
        "--embeds",
        type=int,
        help="User embdedding size",
        default=100,
    )

    parser.add_argument(
        "-B",
        "--batch",
        type=int,
        help="Batch size",
        default=16,
    )

    parser.add_argument(
        "-N",
        "--neg",
        type=int,
        help="Number of negative samples",
        default=20,
    )

    parser.add_argument(
        "-L",
        "--log",
        action="store_true",
        help="Log via wandb",
        default=True,
    )

    args = vars(parser.parse_args())
    return args


if __name__ == "__main__":

    args = get_args()

    BATCH_SIZE = args["batch"]
    EMB_SIZE = args["embeds"]
    NEG = args["neg"]

    LOG = True
    LOG_EVERY = 100

    HOP_SIZE = 0.2
    AUDIO_LEN = 5
    EPOCHS = 1000
    TEMP = 0.07

    if LOG:
        load_dotenv()
        WANDB_SECRET = os.getenv("WANDB_SECRET")
        wandb.login(key=WANDB_SECRET)
    if LOG:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        wandb.init(
            # set the wandb project where this run will be logged
            project="BIO",
            name="run_" + timestamp,
            config={
                "emb_size": EMB_SIZE,
                "batch_size": BATCH_SIZE,
                "temp": TEMP,
            },
        )

    membs_path = "usrembeds/data/embeddings/batched"
    stats_path = "clean_stats.csv"

    dataset = ContrDataset(
        membs_path,
        stats_path,
        nneg=NEG,
        multiplier=10,
        transform=None,
    )

    train_dataset, val_dataset = torch.utils.data.random_split(dataset, [0.8, 0.2])

    NUSERS = dataset.nusers
    train_dataloader = torch.utils.data.DataLoader(
        train_dataset, batch_size=BATCH_SIZE, shuffle=True
    )
    val_dataloader = torch.utils.data.DataLoader(
        val_dataset, batch_size=BATCH_SIZE, shuffle=True
    )

    model = Aligner(
        n_users=NUSERS,
        emb_size=EMB_SIZE,
        prj_size=512,
        prj_type="ln",
    ).to(DEVICE)

    opt = optim.AdamW(model.parameters(), lr=0.001)

    for epoch in range(EPOCHS):

        print(f"Epoch {epoch}")
        losses = []

        for itr, tracks in tqdm(enumerate(train_dataloader)):

            # [B]
            # [B, 1, EMB]
            # [B, NNEG, EMB]
            idx, posemb, negemb = tracks

            idx = idx.to(DEVICE)
            posemb = posemb.to(DEVICE)
            negemb = negemb.to(DEVICE)
            opt.zero_grad()

            ellemb = torch.cat((posemb, negemb), dim=1)

            urs_x, embs = model(idx, ellemb)

            # breakpoint()
            posemb_out = embs[
                :,
                0,
            ].unsqueeze(dim=1)
            negemb_out = embs[
                :,
                1:,
            ]

            # breakpoint()
            out = urs_x.unsqueeze(1)

            loss = contrastive_loss(out, posemb, negemb)
            # breakpoint()
            if itr % LOG_EVERY == 0 and LOG:
                wandb.log(
                    {
                        "loss": loss.item(),
                    }
                )

            losses.append(loss.item())

            loss.backward()
            opt.step()

        val_acc = eval_loop(model, val_dataloader)

        if LOG:
            wandb.log(
                {
                    "mean_loss": np.mean(losses),
                    "val_acc": val_acc,
                }
            )

        print(f"loss {np.mean(losses)} val_acc {round(val_acc,3)}")
        # print(loss)

if LOG:
    wandb.finish()
