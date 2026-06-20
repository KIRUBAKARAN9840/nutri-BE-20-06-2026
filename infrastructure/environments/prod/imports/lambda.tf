# ─────────────────────────────────────────────────────────────────────────
# Import blocks — Lambda functions + EventBridge rules
#
# Phase 3.12.
# ─────────────────────────────────────────────────────────────────────────

# # Lambdas (8 total)
# import {
#   to = module.lambda_reminder.aws_lambda_function.this
#   id = "lambda_reminder"
# }
# import {
#   to = module.lambda_generalReminder_subscriber.aws_lambda_function.this
#   id = "lambda_generalReminder_subscriber"
# }
# import {
#   to = module.lambda_generalReminder_producer.aws_lambda_function.this
#   id = "lambda_generalReminder_producer"
# }
# import {
#   to = module.lambda_feed_post.aws_lambda_function.this
#   id = "lambda_feed_post"
# }
# import {
#   to = module.lambda_attendance_exp_producer.aws_lambda_function.this
#   id = "lambda-attendance-exp-producer"
# }
# import {
#   to = module.lambda_attendance_out_punch.aws_lambda_function.this
#   id = "attendance-out-punch"
# }
# import {
#   to = module.lambda_feedcompressor.aws_lambda_function.this
#   id = "feedcompressor"
# }
# import {
#   to = module.lambda_receipt_mail.aws_lambda_function.this
#   id = "receipt_mail"
# }
#
# # EventBridge rules
# import {
#   to = module.events.aws_cloudwatch_event_rule.daily_analysis
#   id = "daily_analysis_role"
# }
# import {
#   to = module.events.aws_cloudwatch_event_rule.compressor_image
#   id = "compressor_image"
# }
