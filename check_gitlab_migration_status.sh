doco exec gitlab gitlab-rails runner -e production 'puts Gitlab::BackgroundMigration.remaining'

doco exec gitlab gitlab-rails runner -e production 'puts Gitlab::Database::BackgroundMigration::BatchedMigration.queued.count'
