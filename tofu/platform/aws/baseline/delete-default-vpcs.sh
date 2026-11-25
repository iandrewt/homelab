#!/usr/bin/env bash
# Deletes AWS_REGION's default VPC, as AWS_PROFILE
set -euo pipefail

for vpc in $(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[].VpcId' --output text); do
  for igw in $(aws ec2 describe-internet-gateways --filters "Name=attachment.vpc-id,Values=$vpc" \
    --query 'InternetGateways[].InternetGatewayId' --output text); do
    aws ec2 detach-internet-gateway --internet-gateway-id "$igw" --vpc-id "$vpc"
    aws ec2 delete-internet-gateway --internet-gateway-id "$igw"
  done

  for subnet in $(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$vpc" --query 'Subnets[].SubnetId' --output text); do
    aws ec2 delete-subnet --subnet-id "$subnet"
  done

  aws ec2 delete-vpc --vpc-id "$vpc"
done
