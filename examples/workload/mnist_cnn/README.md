# MNIST CNN on Ray

Train a tiny CNN with `astroai run` on a CANFAR `ray-manager` cluster.

1. AstroAI hub: **Start batch compute** (or `astroai cluster start`)
2. Then the job:

```bash
export ASTROAI_RAY_JOBS_ADDRESS=…   # skip inside the manager session
cd examples/workload/mnist_cnn
pip install torch torchvision       # in the job cwd / project, not a CLI extra
astroai run train.py --epochs 1 --ckpt /arc/home/$USER/mnist.pt
python infer.py --ckpt /arc/home/$USER/mnist.pt
```

Write checkpoints under `/arc`, not `/scratch`. Torch is not a dependency of
`astroai`; install it in the project the job runs from.

Use `run`. The Dashboard can still show the same job after it is submitted.
