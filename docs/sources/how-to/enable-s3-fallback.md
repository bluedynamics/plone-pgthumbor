<!-- diataxis: how-to -->

# Enable S3 Fallback for Large Blobs

zodb-pgjsonb supports two-tier blob storage: small blobs are stored inline in
PostgreSQL (the `data` column of `blob_state` as `bytea`), while large blobs
can be offloaded to S3 (the `s3_key` column stores the object key).  The
Thumbor blob loader handles both tiers transparently.

## How Two-Tier Storage Works

When the loader fetches a blob from the `blob_state` table, it checks:

1. **`data` column (PostgreSQL bytea)** -- If not null, the blob bytes are
   returned directly from PostgreSQL.  This is the preferred path for
   performance.
2. **`s3_key` column** -- If `data` is null but `s3_key` is set, the loader
   downloads the blob from S3 using boto3.
3. **Neither** -- If both are null, the loader returns a 500 error (this
   should not happen in normal operation).

PostgreSQL is always checked first.  S3 is only used as a fallback when the
`data` column is null.

## Configure Thumbor

Add the S3 settings to `thumbor.conf`:

```python
# Required: S3 bucket name
PGTHUMBOR_S3_BUCKET = "my-plone-blobs"

# Optional: AWS region (default: us-east-1)
PGTHUMBOR_S3_REGION = "eu-central-1"

# Optional: custom endpoint for S3-compatible services (MinIO, Ceph, etc.)
# Leave empty for AWS S3.
PGTHUMBOR_S3_ENDPOINT = ""
```

Or set via environment variables:

```bash
export PGTHUMBOR_S3_BUCKET="my-plone-blobs"
export PGTHUMBOR_S3_REGION="eu-central-1"
export PGTHUMBOR_S3_ENDPOINT=""
```

## IAM Credentials

The loader uses boto3's standard credential chain.  In order of precedence:

1. **Environment variables:** `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
   `AWS_SESSION_TOKEN`
2. **Shared credentials file:** `~/.aws/credentials`
3. **IAM instance profile** (EC2, ECS task role, EKS service account)
4. **Container credentials** (ECS via `AWS_CONTAINER_CREDENTIALS_RELATIVE_URI`)

For production on AWS, use IAM roles (instance profile or ECS task role)
rather than static credentials.

The minimum IAM policy requires `s3:GetObject` on the blob bucket:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "s3:GetObject",
            "Resource": "arn:aws:s3:::my-plone-blobs/*"
        }
    ]
}
```

## MinIO (S3-Compatible)

For self-hosted S3-compatible storage, set the custom endpoint:

```python
PGTHUMBOR_S3_ENDPOINT = "http://minio:9000"
```

MinIO credentials are set via the standard environment variables:

```bash
export AWS_ACCESS_KEY_ID="minioadmin"
export AWS_SECRET_ACCESS_KEY="minioadmin"
export PGTHUMBOR_S3_BUCKET="my-plone-blobs"
export PGTHUMBOR_S3_ENDPOINT="http://minio:9000"
```

## Disk Cache with S3

When both S3 and the disk cache are enabled, the loader caches S3-fetched
blobs locally.  Subsequent requests for the same blob are served from disk
without an S3 round-trip:

```python
PGTHUMBOR_CACHE_DIR = "/var/cache/thumbor/blobs"
PGTHUMBOR_CACHE_MAX_SIZE = 2147483648  # 2 GB

PGTHUMBOR_S3_BUCKET = "my-plone-blobs"
PGTHUMBOR_S3_REGION = "eu-central-1"
```

This is especially useful for S3-tier blobs, where network latency to S3 is
significantly higher than local disk reads.

## Verify S3 Fallback

1. Upload a large image to Plone.
2. Confirm the blob row exists in PostgreSQL:

   ```sql
   SELECT zoid, tid, blob_size, data IS NOT NULL AS has_data, s3_key
   FROM blob_state
   ORDER BY tid DESC
   LIMIT 5;
   ```

3. For a blob stored in S3, the `has_data` column will be `false` and
   `s3_key` will contain the S3 object key.

4. Request the image through Thumbor.  The loader fetches it from S3 and
   caches it locally (if disk cache is enabled).

5. Check Thumbor logs for any S3 download errors:

   ```
   ERROR:zodb_pgjsonb_thumborblobloader.s3:S3 download failed for key=...: NoSuchKey
   ```

   This means the object key in the database does not match an object in the
   S3 bucket.
