output "helm_release_name" {
  value = helm_release.karpenter.name
}

output "helm_release_version" {
  value = helm_release.karpenter.version
}
