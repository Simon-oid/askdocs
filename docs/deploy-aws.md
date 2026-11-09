# Deploying askdocs on AWS

A guide to running the Compose stack on one EC2 GPU instance. It is
**written and checked locally, not yet run on AWS**:

- `compose.yaml` was run end to end on CPU and on an AMD GPU.
- `compose.cuda.yaml` and `deploy/aws/user-data.sh` were checked only for
  syntax and config (`docker compose config`, `bash -n`): there is no NVIDIA
  GPU here.

Treat the first deploy as the test of those two files.

> **It costs money from the first minute.** GPU instances are not in the AWS
> free tier, and none of the free-tier instances has the memory for this stack
> (it uses about 6.2 GB of RAM). Check the hourly price for your region on the
> EC2 pricing page before launching, and run the teardown at the end.
> A stopped instance stops the compute bill, but its disk is still billed.

## What runs where

One instance, the same stack as on a laptop:

```
your machine ──SSM port forward──► EC2 g4dn.xlarge (no inbound ports)
                                    ├─ askdocs (+ crag-mcp over stdio)  :8000
                                    ├─ llm      llama.cpp, CUDA image   :8081
                                    ├─ ollama   embedder, CPU           :11434
                                    └─ volumes: models, ollama, data (index)
```

Why one instance, and not ECS or EKS:

- askdocs and crag-mcp are one process pair (stdio), and the model server
  needs one GPU.
- For one node, an orchestrator adds GPU scheduling, service discovery and a
  registry, and none of them changes what the service does.
- Scaling out is a separate decision (see the end).

**Instance.** `g4dn.xlarge`: NVIDIA T4 with 16 GB, 4 vCPU, 16 GiB RAM.
- The 3B model at Q4_K_M took 3.4 GB of VRAM here.
- The containers used about 6.2 GB of RAM (llm 5.4, ollama 0.8, askdocs
  0.06).
- Any G-family instance with an NVIDIA GPU works the same way.

**Disk.** 60 GB gp3:
- the CUDA image is 2.6 GB compressed;
- the model, the embedder and the askdocs image add about 3 GB;
- the AMI itself takes a large part of the root volume.

## Before you start

- An AWS account, and the AWS CLI v2 configured (`aws configure`) for a region
  that has G instances.
- **The vCPU quota.** New accounts often have a quota of 0 for "Running
  On-Demand G and VT instances". Check it under Service Quotas → EC2, and
  request at least 4 vCPU. Approval can take a while, so do this first.
- The AWS Session Manager plugin for the CLI, for the port forward.

## 1. A role that allows Session Manager

No SSH key and no open port: access goes through Systems Manager.

```sh
aws iam create-role --role-name askdocs-ec2 \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
    "Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --role-name askdocs-ec2 \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam create-instance-profile --instance-profile-name askdocs-ec2
aws iam add-role-to-instance-profile --instance-profile-name askdocs-ec2 --role-name askdocs-ec2
```

## 2. A security group with no inbound rules

```sh
VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
        --query 'Vpcs[0].VpcId' --output text)
SG=$(aws ec2 create-security-group --group-name askdocs --vpc-id "$VPC" \
        --description "askdocs: no inbound; reach it through SSM" \
        --query GroupId --output text)
```

Outbound stays open: the instance downloads images, the models and the corpus
sources.

**askdocs has no authentication.** Opening port 8000 to the internet would
let anyone spend your GPU time. Do not add an inbound rule for it.

## 3. The AMI

The Deep Learning Base GPU AMI ships the NVIDIA driver, Docker and the NVIDIA
Container Toolkit. AWS publishes its latest id in SSM:

```sh
AMI=$(aws ssm get-parameter \
  --name /aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id \
  --query Parameter.Value --output text)
```

If that parameter name has changed, search the EC2 console's AMI catalog for
"Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu)".

## 4. Launch

```sh
INSTANCE=$(aws ec2 run-instances --image-id "$AMI" --instance-type g4dn.xlarge \
  --iam-instance-profile Name=askdocs-ec2 --security-group-ids "$SG" \
  --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=60,VolumeType=gp3}' \
  --user-data file://deploy/aws/user-data.sh \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=askdocs}]' \
  --query 'Instances[0].InstanceId' --output text)
echo "$INSTANCE"
```

The user data clones the repository, installs the compose plugin if the AMI
lacks it, and runs `docker compose -f compose.yaml -f compose.cuda.yaml up -d
--build`.

The first boot pulls the images, the model and the embedder, and builds the
index. Expect about 10–15 minutes. Edit `REF` in the script to deploy a
pinned commit instead of `main`.

## 5. Check it

```sh
aws ssm start-session --target "$INSTANCE"          # a shell on the instance
sudo tail -f /var/log/askdocs-setup.log
cd /opt/askdocs && sudo docker compose ps           # askdocs, llm, ollama healthy
```

From your machine, forward the API and ask a question:

```sh
aws ssm start-session --target "$INSTANCE" --document-name AWS-StartPortForwardingSession \
  --parameters portNumber=8000,localPortNumber=8000
curl -s 'localhost:8000/ask?stream=false' -H 'content-type: application/json' \
  -d '{"question": "What BM25 parameters does cRAG use?"}'
curl -s localhost:8000/stats
```

To check answer quality and not just that the service is up, run the evals
on the instance and compare with the committed baseline. Both commands run
inside the askdocs image, so the host needs nothing installed:

```sh
cd /opt/askdocs && sudo mkdir -p -m 777 /tmp/ev
sudo docker compose run --rm --no-deps -v "$PWD/evals:/app/evals:ro" -v /tmp/ev:/out \
  askdocs python evals/run.py --phase answer --out /out
sudo docker compose run --rm --no-deps -v "$PWD/evals:/app/evals:ro" -v /tmp/ev:/out \
  askdocs python evals/gate.py compare evals/results /out
```

Expect `ok` everywhere, but not identical retrieval. Ollama in the container
embeds on the CPU, and the committed index was embedded on a GPU. The vectors
agree to cosine 0.99998, which is still enough to swap near-tied passages.
The local Docker run measured this: 3 of 45 rankings changed, and recall@1
went up by one case. An index belongs to the embedder **and** the hardware
it ran on.

## 6. Tracing (optional)

Run Jaeger alongside the stack:

```sh
cd /opt/askdocs
sudo ASKDOCS_OTEL_ENDPOINT=http://jaeger:4318 docker compose \
  -f compose.yaml -f compose.cuda.yaml --profile tracing up -d
```

Then forward the UI port from your machine:

```sh
aws ssm start-session --target "$INSTANCE" --document-name AWS-StartPortForwardingSession \
  --parameters portNumber=16686,localPortNumber=16686
```

On AWS proper, the OTLP endpoint can be an AWS Distro for OpenTelemetry
collector that exports to X-Ray. askdocs only needs the OTLP/HTTP URL.

## 7. Tear down

```sh
aws ec2 terminate-instances --instance-ids "$INSTANCE"
aws ec2 wait instance-terminated --instance-ids "$INSTANCE"
aws ec2 delete-security-group --group-id "$SG"
aws iam remove-role-from-instance-profile --instance-profile-name askdocs-ec2 --role-name askdocs-ec2
aws iam delete-instance-profile --instance-profile-name askdocs-ec2
aws iam detach-role-policy --role-name askdocs-ec2 \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam delete-role --role-name askdocs-ec2
```

Terminating deletes the root volume (the default for volumes created at
launch). Then check the EC2 console for anything left behind: volumes,
snapshots, Elastic IPs.

## What this is not

This setup is fine for a demo. For real users it lacks:

- **Authentication and TLS.** Put an Application Load Balancer with an
  authenticating listener (Cognito or OIDC) and an ACM certificate in front,
  or an API gateway. Only then open a port.
- **Persistence.** The index and the models live in Docker volumes on one
  disk. Both can be rebuilt, but losing them costs a rebuild.
- **Scaling.** One GPU answers `MAX_CONCURRENT` (4) requests at once, and the
  next one gets `503`. Scaling out means more instances behind the load
  balancer. Each one carries its own index copy, which is fine for a
  read-only corpus.
- **No GPU at all.** With `ASKDOCS_LLM_PROVIDER=openai` and a hosted model,
  the stack minus `llm` runs on a CPU instance: embedding on CPU costs about
  30 ms per question here. The money then goes into tokens instead (about
  2000 in and 33 out per answer, see [observability](observability.md)).
- **CPU-only llama.cpp is not an option for users.** It answered in about
  14 s instead of 0.7 s, almost all of it spent reading the 2000-token
  prompt.
