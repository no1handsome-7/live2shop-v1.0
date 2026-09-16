"""
Register checkpoint lineage in MLflow without pretending the custom multimodal
runtime is an MLflow serving model. The actual serving container loads the same
immutable checkpoint directory via the local champion symlink.
"""
import argparse,os
from pathlib import Path
import mlflow

ap=argparse.ArgumentParser()
ap.add_argument("--checkpoint",required=True)
ap.add_argument("--family",required=True)
ap.add_argument("--stage",required=True)
ap.add_argument("--tracking-uri",default=os.getenv("MLFLOW_TRACKING_URI","http://localhost:5000"))
ap.add_argument("--metric",action="append",default=[],help="name=value")
a=ap.parse_args()
mlflow.set_tracking_uri(a.tracking_uri);mlflow.set_experiment(a.family)
with mlflow.start_run(run_name=f"{a.family}-{a.stage}") as run:
    mlflow.log_param("family",a.family);mlflow.log_param("stage",a.stage)
    for x in a.metric:
        k,v=x.split("=",1);mlflow.log_metric(k,float(v))
    mlflow.log_artifacts(a.checkpoint,artifact_path="checkpoint")
    print("run_id",run.info.run_id)
