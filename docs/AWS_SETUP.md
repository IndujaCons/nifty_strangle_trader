# AWS EC2 Setup Guide - Scheduled Start/Stop

This guide sets up the NIFTY Strangle Trader on AWS EC2 with automatic start/stop during market hours.

**Estimated Cost: ~$2-3/month** (after free tier)

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    AWS Cloud                            │
│                                                         │
│  ┌──────────────┐     ┌──────────────┐                 │
│  │ EventBridge  │────▶│    Lambda    │                 │
│  │ (Scheduler)  │     │ (Start/Stop) │                 │
│  └──────────────┘     └──────┬───────┘                 │
│                              │                          │
│                              ▼                          │
│                    ┌──────────────────┐                │
│                    │   EC2 t4g.micro  │                │
│                    │  (Your App)      │                │
│                    │  Port 8080       │                │
│                    └──────────────────┘                │
│                              │                          │
│                              ▼                          │
│                    ┌──────────────────┐                │
│                    │   Elastic IP     │                │
│                    │  (Static IP)     │                │
│                    └──────────────────┘                │
└─────────────────────────────────────────────────────────┘
```

## Prerequisites

- AWS Account
- Basic familiarity with AWS Console
- SSH key pair for EC2 access

---

## Step 1: Create EC2 Instance

### 1.1 Launch Instance

1. Go to **EC2 Console** → **Launch Instance**

2. **Name**: `nifty-strangle-trader`

3. **AMI**: Amazon Linux 2023 (ARM64)
   - Select "64-bit (Arm)" architecture

4. **Instance Type**: `t4g.micro`
   - 2 vCPU, 1 GB RAM
   - Free tier eligible (750 hours/month first year)

5. **Key Pair**: Create new or select existing
   - Download and save the `.pem` file securely

6. **Network Settings** → Edit:
   - Auto-assign Public IP: **Enable**
   - Create security group:
     - Name: `strangle-trader-sg`
     - Inbound rules:
       ```
       SSH (22)    - Your IP only (for security)
       Custom TCP (8080) - Your IP only (or 0.0.0.0/0 if needed)
       ```

7. **Storage**: 8 GB gp3 (default is fine)

8. **Launch Instance**

### 1.2 Allocate Elastic IP (Static IP)

1. Go to **EC2** → **Elastic IPs** → **Allocate Elastic IP**
2. Click **Allocate**
3. Select the new IP → **Actions** → **Associate Elastic IP**
4. Select your instance → **Associate**

**Note**: Elastic IP is free when associated with a running instance. You'll be charged ~$0.005/hour when instance is stopped (negligible).

---

## Step 2: Connect and Setup Instance

### 2.1 Connect via SSH

```bash
# Make key file secure
chmod 400 your-key.pem

# Connect (replace with your Elastic IP)
ssh -i your-key.pem ec2-user@YOUR_ELASTIC_IP
```

### 2.2 Install Dependencies

```bash
# Update system
sudo yum update -y

# Install Python 3.11 and Git
sudo yum install python3.11 python3.11-pip git -y

# Verify installation
python3.11 --version
```

### 2.3 Clone and Setup Application

```bash
# Clone repository
cd /home/ec2-user
git clone https://github.com/IndujaCons/nifty_strangle_trader.git
cd nifty_strangle_trader

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Create .env file
cat > .env << 'EOF'
KITE_API_KEY=your_api_key_here
KITE_API_SECRET=your_api_secret_here
KITE_ACCESS_TOKEN=

PAPER_TRADING=true
LOT_QUANTITY=1
TARGET_DELTA=0.07
MOVE_DECAY_THRESHOLD=0.60
AUTO_TRADE=false
EOF

# Edit with your actual credentials
nano .env
```

### 2.4 Test Application

```bash
# Test run (Ctrl+C to stop)
source venv/bin/activate
python run.py --ui
```

Visit `http://YOUR_ELASTIC_IP:8080` in your browser.

---

## Step 3: Create Systemd Service

This ensures the app starts automatically when the instance boots.

### 3.1 Create Service File

```bash
sudo nano /etc/systemd/system/strangle-trader.service
```

Paste the following:

```ini
[Unit]
Description=NIFTY Strangle Trader
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/nifty_strangle_trader
Environment="PATH=/home/ec2-user/nifty_strangle_trader/venv/bin"
ExecStart=/home/ec2-user/nifty_strangle_trader/venv/bin/python run.py --ui
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 3.2 Enable and Start Service

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable service (start on boot)
sudo systemctl enable strangle-trader

# Start service now
sudo systemctl start strangle-trader

# Check status
sudo systemctl status strangle-trader

# View logs
sudo journalctl -u strangle-trader -f
```

---

## Step 4: Setup Scheduled Start/Stop

### 4.1 Create IAM Role for Lambda

1. Go to **IAM** → **Roles** → **Create Role**

2. **Trusted Entity**: AWS Service → Lambda

3. **Permissions**: Create inline policy:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "ec2:StartInstances",
                "ec2:StopInstances"
            ],
            "Resource": "arn:aws:ec2:ap-south-1:YOUR_ACCOUNT_ID:instance/YOUR_INSTANCE_ID"
        },
        {
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            "Resource": "*"
        }
    ]
}
```

4. **Role Name**: `lambda-ec2-scheduler-role`

### 4.2 Create Lambda Function - Start Instance

1. Go to **Lambda** → **Create Function**

2. **Function name**: `start-strangle-trader`

3. **Runtime**: Python 3.11

4. **Architecture**: arm64 (cheaper)

5. **Permissions**: Use existing role → `lambda-ec2-scheduler-role`

6. **Code**:

```python
import boto3

def lambda_handler(event, context):
    ec2 = boto3.client('ec2', region_name='ap-south-1')

    # Replace with your instance ID
    instance_id = 'i-xxxxxxxxxxxxxxxxx'

    try:
        ec2.start_instances(InstanceIds=[instance_id])
        print(f'Started instance: {instance_id}')
        return {
            'statusCode': 200,
            'body': f'Started instance: {instance_id}'
        }
    except Exception as e:
        print(f'Error: {str(e)}')
        return {
            'statusCode': 500,
            'body': str(e)
        }
```

7. **Deploy**

### 4.3 Create Lambda Function - Stop Instance

1. **Function name**: `stop-strangle-trader`

2. Same settings as above

3. **Code**:

```python
import boto3

def lambda_handler(event, context):
    ec2 = boto3.client('ec2', region_name='ap-south-1')

    # Replace with your instance ID
    instance_id = 'i-xxxxxxxxxxxxxxxxx'

    try:
        ec2.stop_instances(InstanceIds=[instance_id])
        print(f'Stopped instance: {instance_id}')
        return {
            'statusCode': 200,
            'body': f'Stopped instance: {instance_id}'
        }
    except Exception as e:
        print(f'Error: {str(e)}')
        return {
            'statusCode': 500,
            'body': str(e)
        }
```

### 4.4 Create EventBridge Schedules

#### Schedule 1: Start Instance (8:45 AM IST = 3:15 AM UTC)

1. Go to **Amazon EventBridge** → **Schedules** → **Create Schedule**

2. **Schedule name**: `start-strangle-trader-morning`

3. **Schedule pattern**: Recurring schedule
   - **Cron expression**: `15 3 ? * MON-FRI *`
   - (Runs at 3:15 AM UTC = 8:45 AM IST, Monday to Friday)

4. **Flexible time window**: Off

5. **Target**: AWS Lambda → `start-strangle-trader`

6. **Create**

#### Schedule 2: Stop Instance (4:00 PM IST = 10:30 AM UTC)

1. **Schedule name**: `stop-strangle-trader-evening`

2. **Cron expression**: `30 10 ? * MON-FRI *`
   - (Runs at 10:30 AM UTC = 4:00 PM IST, Monday to Friday)

3. **Target**: AWS Lambda → `stop-strangle-trader`

---

## Step 5: Setup Daily Login Reminder (Optional)

Since Zerodha tokens expire daily, you can set up SNS notification.

### 5.1 Create SNS Topic

1. Go to **SNS** → **Create Topic**
2. **Type**: Standard
3. **Name**: `strangle-trader-alerts`
4. Create subscription → Email → Your email

### 5.2 Modify Start Lambda to Send Notification

```python
import boto3

def lambda_handler(event, context):
    ec2 = boto3.client('ec2', region_name='ap-south-1')
    sns = boto3.client('sns', region_name='ap-south-1')

    instance_id = 'i-xxxxxxxxxxxxxxxxx'
    topic_arn = 'arn:aws:sns:ap-south-1:YOUR_ACCOUNT_ID:strangle-trader-alerts'

    try:
        ec2.start_instances(InstanceIds=[instance_id])

        # Send reminder to login
        sns.publish(
            TopicArn=topic_arn,
            Subject='Strangle Trader Started - Login Required',
            Message='Your trading system has started.\n\nPlease login to Zerodha at:\nhttp://YOUR_ELASTIC_IP:8080\n\nRemember to complete the daily Kite Connect authentication.'
        )

        return {'statusCode': 200, 'body': 'Started and notified'}
    except Exception as e:
        return {'statusCode': 500, 'body': str(e)}
```

Add SNS permission to Lambda role:
```json
{
    "Effect": "Allow",
    "Action": "sns:Publish",
    "Resource": "arn:aws:sns:ap-south-1:YOUR_ACCOUNT_ID:strangle-trader-alerts"
}
```

---

## Cost Breakdown

| Resource | Cost |
|----------|------|
| EC2 t4g.micro (7 hrs × 22 days) | ~$1.30/month |
| EBS 8GB gp3 | ~$0.80/month |
| Elastic IP (stopped hours) | ~$0.50/month |
| Lambda (60 invocations) | Free tier |
| EventBridge | Free tier |
| **Total** | **~$2.60/month** |

---

## Maintenance Commands

### SSH into Instance
```bash
ssh -i your-key.pem ec2-user@YOUR_ELASTIC_IP
```

### View Application Logs
```bash
sudo journalctl -u strangle-trader -f
```

### Restart Application
```bash
sudo systemctl restart strangle-trader
```

### Update Application
```bash
cd /home/ec2-user/nifty_strangle_trader
git pull
sudo systemctl restart strangle-trader
```

### Check Instance Status
```bash
aws ec2 describe-instance-status --instance-ids YOUR_INSTANCE_ID
```

---

## Troubleshooting

### App not accessible after instance start
- Wait 1-2 minutes for boot and service start
- Check security group allows your IP on port 8080
- Check service status: `sudo systemctl status strangle-trader`

### Lambda not starting/stopping instance
- Check IAM role has correct permissions
- Verify instance ID in Lambda code
- Check CloudWatch Logs for Lambda errors

### Service fails to start
- Check logs: `sudo journalctl -u strangle-trader -n 50`
- Verify Python path in service file
- Test manually: `cd /home/ec2-user/nifty_strangle_trader && ./venv/bin/python run.py --ui`

---

## Security Best Practices

1. **Restrict SSH access** to your IP only
2. **Restrict port 8080** to your IP only
3. **Never commit .env file** to git
4. **Use AWS Secrets Manager** for credentials (advanced)
5. **Enable MFA** on your AWS account
6. **Set billing alerts** in AWS Budgets

---

## Quick Reference

| Item | Value |
|------|-------|
| Instance Type | t4g.micro |
| Region | ap-south-1 (Mumbai) |
| App URL | http://YOUR_ELASTIC_IP:8080 |
| Start Time | 8:45 AM IST (Mon-Fri) |
| Stop Time | 4:00 PM IST (Mon-Fri) |
| SSH User | ec2-user |
| App Directory | /home/ec2-user/nifty_strangle_trader |
| Service Name | strangle-trader |
