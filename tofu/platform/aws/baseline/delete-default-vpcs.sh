#!/usr/bin/env bash
# Deletes AWS_REGION's default VPC, as ROLE_ARN when it's set
set -euo pipefail

if [[ -n ${ROLE_ARN:-} ]]; then
  read -r AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN < <(
    aws sts assume-role --role-arn "$ROLE_ARN" --role-session-name baseline \
      --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' --output text
  )
  export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
  unset AWS_PROFILE
fi

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
