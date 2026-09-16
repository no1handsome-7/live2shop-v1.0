import argparse,os,shutil
from pathlib import Path

ap=argparse.ArgumentParser()
ap.add_argument("--checkpoint",required=True)
ap.add_argument("--family",required=True,choices=["commerce-vlm","rec-model"])
ap.add_argument("--root",default="checkpoints")
a=ap.parse_args()

src=Path(a.checkpoint).resolve()
dst=Path(a.root)/a.family/"champion"
dst.parent.mkdir(parents=True,exist_ok=True)
if dst.is_symlink() or dst.exists():
    if dst.is_symlink() or dst.is_file():dst.unlink()
    else:shutil.rmtree(dst)
# Relative symlink remains valid inside Docker bind mount.
dst.symlink_to(os.path.relpath(src,dst.parent),target_is_directory=True)
print(dst,"->",os.readlink(dst))
