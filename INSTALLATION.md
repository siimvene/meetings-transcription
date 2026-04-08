# Installation Guide

Step-by-step guide for deploying the on-premise Teams meeting transcription system. This guide assumes a fresh setup. Adapt paths, domains, and credentials to your environment.

## Prerequisites

| Requirement | Purpose |
|---|---|
| **GPU server** (Linux) | Runs Whisper, Sortformer, LLM, and all backend services. Minimum 24 GB free VRAM. |
| **Windows Server** (2022/2025, Core or Desktop) | Runs the Teams media bot. Needs a public IP. Can be a VM anywhere (Azure, AWS, on-premise). |
| **Azure Entra ID tenant** | The Microsoft 365 tenant where Teams users are. Needed for bot registration and SSO. |
| **Global Admin or Application Administrator** | Required once, to grant API permissions to the bot. |
| **Docker + NVIDIA Container Toolkit** | On the GPU server, for running the transcription pipeline. |
| **Domain name** (optional) | For TLS certificate on the Windows server. An Azure-assigned DNS label works too. |

## Overview

```
Phase 1: Azure Entra ID setup (app registrations, permissions)
Phase 2: GPU server setup (Docker, models, services)
Phase 3: Windows server setup (.NET, bot, TLS certificate)
Phase 4: Teams app deployment (upload to Teams Admin Center)
Phase 5: Verification (test with a real meeting)
```

---

## Phase 1: Azure Entra ID Setup

### 1.1 Register the Bot Application

In the Azure Portal of your Microsoft 365 tenant:

1. Go to **Entra ID** > **App registrations** > **New registration**
2. Configure:
   - **Name**: `MeetingTranscriptionBot`
   - **Supported account types**: Single tenant
   - **Redirect URI**: leave empty
3. After creation, note the **Application (client) ID** and **Directory (tenant) ID**
4. Go to **Certificates & secrets** > **New client secret**
   - Note the secret **Value** (not the ID)

### 1.2 Configure Bot API Permissions

In the bot's app registration:

1. Go to **API permissions** > **Add a permission** > **Microsoft Graph** > **Application permissions**
2. Add these three permissions:
   - `Calls.JoinGroupCall.All` — join meetings
   - `Calls.AccessMedia.All` — capture audio
   - `Chat.ReadWrite.All` — read/write meeting chat messages
3. Click **Grant admin consent for [your org]**
   - This requires Global Administrator or Privileged Role Administrator
   - If you can't do this yourself, send the admin this consent URL:
     ```
     https://login.microsoftonline.com/{TENANT_ID}/adminconsent?client_id={BOT_APP_ID}
     ```

### 1.3 Create the Azure Bot Resource

1. In Azure Portal, search for **Azure Bot** > **Create**
2. Configure:
   - **Bot handle**: `MeetingTranscriptionBot`
   - **Microsoft App ID**: paste the Application ID from step 1.1
   - **App type**: Single Tenant
3. After creation, go to **Channels** > **Microsoft Teams**
4. Enable the Teams channel
5. Under **Calling**, enable calling and set the webhook URL:
   ```
   https://{YOUR_WINDOWS_SERVER_HOSTNAME}/api/calls
   ```
   (You'll set this hostname in Phase 3)

### 1.4 Register the Web Application (for SSO)

1. Go to **Entra ID** > **App registrations** > **New registration**
2. Configure:
   - **Name**: `MeetingTranscriptViewer`
   - **Supported account types**: Single tenant
   - **Redirect URI**: SPA > `https://{YOUR_GPU_SERVER}/auth/callback`
3. Go to **API permissions** > add **Microsoft Graph** > **Delegated** > `User.Read`
4. Note the **Application (client) ID** and **Directory (tenant) ID**

---

## Phase 2: GPU Server Setup

### 2.1 Install Docker + NVIDIA Container Toolkit

If not already installed:

```bash
# Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# NVIDIA Container Toolkit
# See: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Verify
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
```

### 2.2 Clone and Configure

```bash
git clone <this-repo> ~/meetings-transcription
cd ~/meetings-transcription

# Create environment configuration
cp config/env.example config/.env
```

Edit `config/.env`:

```bash
# Your LLM endpoint (vLLM, Ollama, or any OpenAI-compatible API)
VLLM_BASE_URL=http://host.docker.internal:8000/v1
VLLM_API_KEY=your-api-key
VLLM_MODEL=your-model-name

# Azure Entra ID (from Phase 1.4)
NEXT_PUBLIC_AZURE_CLIENT_ID=<web-app-client-id>
NEXT_PUBLIC_AZURE_TENANT_ID=<tenant-id>

# PostgreSQL (change the password!)
POSTGRES_PASSWORD=your-secure-password
DATABASE_URL=postgresql://meetings:your-secure-password@postgres:5432/meetings

# RabbitMQ (change the password!)
MQ_PASSWORD=your-secure-password
```

### 2.3 Download Translation Models

```bash
# Download TartuNLP septilang NMT model
./scripts/download-models.sh
```

This downloads ~2 GB from HuggingFace. The model files go into `./models/septilang/`.

### 2.4 Start Services

```bash
docker compose up -d

# Watch logs
docker compose logs -f

# Verify all services are healthy
docker compose ps
```

Expected: 9 containers running (transcription, ingestion, assembly, summarizer, translation-worker, rabbitmq, postgres, api, web).

### 2.5 Verify

```bash
# API health
curl http://localhost:8080/health

# Web UI
open http://localhost:3000
```

---

## Phase 3: Windows Server Setup

The Teams media bot requires Windows Server because the Microsoft Graph Communications Media SDK is Windows-only. This server can be hosted anywhere with a public IP.

### 3.1 Provision the Server

**Option A: Azure VM**
```bash
az group create -n teams-bot -l westeurope
az vm create \
  -g teams-bot -n teams-bot \
  --image MicrosoftWindowsServer:WindowsServer:2025-datacenter-core-g2:latest \
  --size Standard_B2ls_v2 \
  --admin-username teams_admin \
  --admin-password '<secure-password>' \
  --public-ip-address-dns-name your-bot-hostname
```

**Option B: On-premise Windows Server**
- Windows Server 2022 or 2025 (Core edition is sufficient)
- Public IP address
- Firewall rules allowing inbound traffic (see below)

### 3.2 Open Firewall Ports

| Port | Protocol | Purpose |
|---|---|---|
| 22 | TCP | SSH (for management) |
| 80 | TCP | HTTP (Let's Encrypt validation) |
| 443 | TCP | HTTPS (Graph webhook) |
| 3478-3481 | UDP | STUN/TURN (Teams media relays) |
| 49152-53247 | UDP | Media relay port range |

**Azure NSG:**
```bash
az network nsg rule create -g teams-bot --nsg-name <nsg-name> -n AllowSSH --priority 1000 --protocol TCP --destination-port-ranges 22
az network nsg rule create -g teams-bot --nsg-name <nsg-name> -n AllowHTTPS --priority 1010 --protocol TCP --destination-port-ranges 80 443
az network nsg rule create -g teams-bot --nsg-name <nsg-name> -n AllowTeamsMedia --priority 1020 --protocol UDP --destination-port-ranges 3478-3481 49152-53247
```

**Windows Firewall (on the server):**
```powershell
New-NetFirewallRule -DisplayName "HTTP" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow
New-NetFirewallRule -DisplayName "HTTPS" -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow
New-NetFirewallRule -DisplayName "Teams Media UDP" -Direction Inbound -Protocol UDP -LocalPort 3478-3481,49152-53247 -Action Allow
```

### 3.3 Install Prerequisites

SSH into the server and run:

```powershell
# Install OpenSSH Server (if not already available)
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic

# Install .NET 8 SDK
Invoke-WebRequest -Uri 'https://dot.net/v1/dotnet-install.ps1' -OutFile 'dotnet-install.ps1'
.\dotnet-install.ps1 -Channel 8.0 -InstallDir 'C:\dotnet'
[Environment]::SetEnvironmentVariable('PATH', $env:PATH + ';C:\dotnet', 'Machine')
```

### 3.4 Get TLS Certificate

Using [win-acme](https://www.win-acme.com/) (free Let's Encrypt certificates):

```powershell
# Download win-acme
$wacsUrl = "https://github.com/win-acme/win-acme/releases/download/v2.2.9.1701/win-acme.v2.2.9.1701.x64.pluggable.zip"
New-Item -ItemType Directory -Force -Path C:\wacs, C:\certs
Invoke-WebRequest -Uri $wacsUrl -OutFile C:\wacs\wacs.zip -UseBasicParsing
Expand-Archive -Path C:\wacs\wacs.zip -DestinationPath C:\wacs -Force

# Request certificate
C:\wacs\wacs.exe `
  --source manual `
  --host your-bot-hostname.region.cloudapp.azure.com `
  --store pemfiles `
  --pemfilespath C:\certs `
  --accepttos `
  --emailaddress admin@yourorg.com `
  --validation selfhosting `
  --closeonfinish
```

The certificate auto-renews via a scheduled task.

### 3.5 Deploy the Bot

```bash
# From your local machine, copy the bot files to the server
scp -r meetings-transcription-bot/src/* teams_admin@<server-ip>:C:/bot/src/

# Also copy the proto file
scp meetings-transcription/proto/audio_ingestion.proto teams_admin@<server-ip>:C:/bot/src/Protos/
```

### 3.6 Configure the Bot

Edit `C:\bot\src\appsettings.json` on the server:

```json
{
  "Bot": {
    "AppId": "<Bot Application ID from Phase 1.1>",
    "AppSecret": "<Bot client secret from Phase 1.1>",
    "TenantId": "<Tenant ID>",
    "BaseUrl": "https://<your-bot-hostname>",
    "MediaPlatformInstanceId": "<generate a new GUID>",
    "CertificatePath": "C:\\certs\\<hostname>-chain.pem",
    "CertificatePassword": "",
    "MediaPublicAddress": "<server-public-ip>",
    "MediaPort": 8445
  },
  "Ingestion": {
    "GrpcEndpoint": "http://<gpu-server-ip>:50051"
  }
}
```

### 3.7 Build and Run

```powershell
cd C:\bot\src
C:\dotnet\dotnet restore
C:\dotnet\dotnet build

# Run (foreground for testing)
C:\dotnet\dotnet run

# Or install as a Windows Service for production
C:\dotnet\dotnet publish -c Release -o C:\bot\publish
sc create MeetingsBot binPath="C:\bot\publish\MeetingsBot.exe" start=auto
sc start MeetingsBot
```

---

## Phase 4: Teams App Deployment

### 4.1 Update the Calling Webhook

Go back to the Azure Bot resource from Phase 1.3:
- **Channels** > **Microsoft Teams** > **Calling** > Webhook URL:
  ```
  https://<your-bot-hostname>/api/calls
  ```

### 4.2 Upload the Teams App

The `TranscriptionBot.zip` in the bot repository contains the Teams app manifest.

**Option A: Teams Admin Center (org-wide)**
1. Go to `https://admin.teams.microsoft.com`
2. **Teams apps** > **Manage apps** > **Upload new app**
3. Upload `TranscriptionBot.zip`
4. The app is now available to all users in the organization

**Option B: Sideload for testing**
1. In Teams, go to **Apps** > **Manage your apps** > **Upload a custom app**
2. Upload `TranscriptionBot.zip`
3. The app is available only to you

### 4.3 Customize the App (optional)

Edit `teams-app/manifest.json` to change:
- `name.short` / `name.full` — the bot's display name
- `description` — what users see in the app store
- `developer` — your organization details
- Replace `icon-color.png` and `icon-outline.png` with your branding

Repackage: `cd teams-app && zip -j ../TranscriptionBot.zip manifest.json icon-outline.png icon-color.png`

---

## Phase 5: Verification

### 5.1 Test the Bot

1. Create a Teams meeting
2. Add **Transcription Bot** as a participant
3. Start the meeting
4. Verify:
   - Bot appears in the participant list
   - Recording indicator shows in Teams
   - Bot posts an intro message to the meeting chat
   - Speak for 1-2 minutes

### 5.2 Check the Pipeline

```bash
# On the GPU server, check service logs
docker compose logs ingestion    # Should show gRPC audio chunks arriving
docker compose logs transcription # Should show Whisper processing audio
docker compose logs assembly     # Should show segments being stored

# Check PostgreSQL
docker compose exec postgres psql -U meetings -c "SELECT count(*) FROM transcript_segments;"
```

### 5.3 Test the Web UI

1. Open `https://<gpu-server>/` (or `http://localhost:3000`)
2. Log in with Azure Entra SSO
3. You should see the meeting you just created
4. Click to view the transcript with speaker names

### 5.4 Test Summarization

1. End the meeting
2. Check the meeting chat — the bot should post a final summary
3. In the web UI, the meeting should now have a summary section

---

## Scaling Considerations

### Multiple Concurrent Meetings

Each meeting requires:
- ~640 bytes/sec per participant (16kHz 16-bit PCM)
- One WhisperLiveKit WebSocket per participant
- Whisper processes audio faster than real-time, so 10+ concurrent meetings are feasible

### GPU Memory

| Component | VRAM |
|---|---|
| LLM for summarization (e.g. Gemma 4 31B at fp8) | varies |
| Whisper large-v3-turbo | ~4 GB |
| Sortformer (room diarization) | ~2 GB |

Ensure your GPU has at least 6 GB free after the LLM allocation.

### LLM Choice

The summarizer calls any OpenAI-compatible API. You can use:
- **vLLM** with any model (Gemma, Llama, Qwen, Mistral)
- **Ollama** for simpler setups
- **Any OpenAI-compatible endpoint**

Set `VLLM_BASE_URL`, `VLLM_API_KEY`, and `VLLM_MODEL` in `config/.env`.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Bot doesn't join meetings | Check admin consent is granted. Verify the calling webhook URL in Azure Bot. Check bot logs for 403 errors. |
| No audio received | Check Windows Firewall UDP ports. Verify NSG rules. Check `nvidia-smi` for GPU memory. |
| Transcription quality is poor | Try a different Whisper model size. Check audio sample rate matches 16kHz. |
| Summary not generated | Check the LLM endpoint is reachable from Docker. Check summarizer logs. |
| Web UI login fails | Verify the Azure Entra web app registration redirect URI matches your domain. |
| Translation not working | Check RabbitMQ is running. Verify the septilang model is downloaded to `./models/`. |

## Security Notes

- All audio processing happens on-premise. No data is sent to external AI services.
- The Windows server communicates with Microsoft's Teams infrastructure (media relays) only for receiving audio.
- The bot reads meeting chat only from the time it joins (not full history).
- Transcripts are stored in PostgreSQL with owner-based access control (only the person who invited the bot can view).
- Use WireGuard or a private network between the Windows server and GPU server if they are in different locations.
