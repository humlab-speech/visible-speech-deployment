#webapi dir should be set so that the www-data user (uid 33) within the edge-router container has write access
chown -R 33 ./mounts/webapi
chown -R 33 ./mounts/apache/apache/uploads
