# TODO: R2 or S3, before the first real apply. Local for now.
#
# terraform {
#   backend "s3" {
#     bucket = "..."
#     key    = "alfheim/terraform.tfstate"
#     region = "..."
#     # native OpenTofu state encryption (encryption.tf) means this bucket never needs
#     # to be trusted with plaintext secrets.
#   }
# }
#
# terraform {
#   backend "s3" {
#     bucket                      = "..."
#     key                         = "alfheim/terraform.tfstate"
#     region                      = "auto"
#     endpoints                   = { s3 = "https://<account-id>.r2.cloudflarestorage.com" }
#     skip_credentials_validation = true
#     skip_region_validation      = true
#     skip_requesting_account_id  = true
#   }
# }

terraform {
  backend "local" {
    path = "terraform.tfstate"
  }
}
