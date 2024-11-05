import torch
from datautils.dataset import MusicDataset
from sklearn.manifold import TSNE
import matplotlib.pyplot as plt
import argparse


def plot_music_batch(emb, device):

    emb_flat = emb.view(-1, emb.shape[2])  # flatten the first two dimensions
    tsne = TSNE(n_components=2)

    emb_2d = tsne.fit_transform(emb_flat.cpu().detach().numpy())
    emb_2d = torch.tensor(emb_2d, device=device).view(
        emb.shape[0], emb.shape[1], 2
    )  # reshape back to [16, 6, 2]

    emb_2d_np = emb_2d.cpu().detach().numpy()

    plt.figure(figsize=(10, 8))
    for i in range(emb_2d_np.shape[0]):
        cluster_points = emb_2d_np[i]
        plt.scatter(cluster_points[:, 0], cluster_points[:, 1], label=f"Cluster {i+1}")

    plt.title("t-SNE Embedding")
    plt.show()


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
        "-S",
        "--subset",
        type=int,
        help="Only load a subset of the data",
        default=100,
    )

    parser.add_argument(
        "-T",
        "--temp",
        type=float,
        help="Temperature for the InfoNCE loss",
        default=0.07,
    )

    parser.add_argument(
        "-M",
        "--multiplier",
        type=int,
        help="Dataset multiplier",
        default=10,
    )

    parser.add_argument(
        "-NL",
        "--no-log",
        action="store_true",
        help="Don't log via wandb",
        default=False,
    )

    args = vars(parser.parse_args())
    return args
