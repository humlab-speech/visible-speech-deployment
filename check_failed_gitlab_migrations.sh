doco exec gitlab gitlab-rails runner -e production 'puts Gitlab::Database::BackgroundMigration::BatchedMigration.failed.count'
doco exec gitlab gitlab-rails runner -e production 'puts Gitlab::Database::BackgroundMigration::BatchedMigration.with_status(:failed).count'
