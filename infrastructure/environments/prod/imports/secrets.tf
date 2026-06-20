# ─────────────────────────────────────────────────────────────────────────
# Import blocks — Secrets Manager + KMS keys
#
# Phases 3.1 and 3.3. IMPORTANT: importing a secret pulls metadata only;
# the secret VALUE is fetched at runtime by your app, not stored in tfstate.
# Still — keep tfstate encrypted (already done by backend bootstrap).
# ─────────────────────────────────────────────────────────────────────────

# # Secrets (7 total)
# import { to = module.secrets.aws_secretsmanager_secret.fittbot_main
#   id = "fittbot/secrets" }
# import { to = module.secrets.aws_secretsmanager_secret.fittbot_staging
#   id = "fittbot/stagingsecrets" }
# import { to = module.secrets.aws_secretsmanager_secret.fittbot_mysql
#   id = "fittbot/mysqldb" }
# import { to = module.secrets.aws_secretsmanager_secret.fittbot_otp
#   id = "fittbot/otpsecrets" }
# import { to = module.secrets.aws_secretsmanager_secret.fittbot_session
#   id = "fittbot/sessiontoken" }
# import { to = module.secrets.aws_secretsmanager_secret.fittbot_reminder
#   id = "fittbot/reminderarn" }
# import { to = module.secrets.aws_secretsmanager_secret.github_token
#   id = "github-token" }
#
# # KMS keys (4 customer-managed)
# import { to = module.kms.aws_kms_alias.dbcredentials
#   id = "alias/fittbot/dbcredentials" }
# import { to = module.kms.aws_kms_alias.mysqldb
#   id = "alias/fittbot/mysqldb" }
# import { to = module.kms.aws_kms_alias.otpsecrets
#   id = "alias/fittbot/otpsecrets" }
# import { to = module.kms.aws_kms_alias.sessiontoken
#   id = "alias/fittbot/sessiontoken" }
#
# # And the keys themselves (use the KeyId, not the alias):
# import { to = module.kms.aws_kms_key.dbcredentials
#   id = "bba3b9c1-9280-41f3-9da4-bf30dea8baa2" }
# import { to = module.kms.aws_kms_key.mysqldb
#   id = "309b5807-7f16-40d4-9bfb-fc923f1b0fcc" }
# import { to = module.kms.aws_kms_key.otpsecrets
#   id = "394d6e5f-6d69-4de5-8ad8-44221805820f" }
# import { to = module.kms.aws_kms_key.sessiontoken
#   id = "010c4386-b00a-4b7a-87b4-4089cdcfeb53" }
