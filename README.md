# Enterprise Outlook Scheduler

Microsoft 365（Outlook）と連携したエンタープライズ向け日程調整・会議室予約アプリケーション。  
Google Cloud Run 上で動作し、Human-In-The-Loop (HITL) 承認フローを実装しています。

## 技術スタック

- **Frontend / Backend**: Python + Streamlit
- **Authentication**: `msal` (Microsoft Authentication Library) + Microsoft Entra ID (委任された権限)
- **API**: Microsoft Graph API
- **Infrastructure**: Google Cloud Run (ステートレスコンテナ)
- **State & Data Store**: Google Cloud Firestore (Datastore モード) / ローカル開発時は SQLite 自動フォールバック
- **Secret Management**: GCP Secret Manager または環境変数

## ファイル構成

```
├── app.py              # Streamlit メインアプリ (ルーティング・UI)
├── auth.py             # MSAL 認証ヘルパー (AuthCode Flow, トークンキャッシュ)
├── db_client.py        # DB クライアント (Datastore / SQLite デュアル対応)
├── graph_client.py     # Microsoft Graph API ラッパー
├── requirements.txt    # Python 依存ライブラリ
├── Dockerfile          # Cloud Run 向け Docker 定義
├── .env.template       # 環境変数テンプレート (シークレットは含まない)
└── Gemini.md           # プロジェクト仕様・設計ドキュメント
```

## 機能

- **Microsoft Entra ID 認証**: OAuth 2.0 Authorization Code Flow による委任認証
- **会議室一覧取得 & フィルタリング**: `GET /places/microsoft.graph.room` + ページネーション対応
- **空き時間検索**: `POST /me/findMeetingTimes` で参加者・会議室の空き時間を一括検索
- **HITL 承認フロー**:
  1. 申請者が候補枠を選択 → Firestore に `pending` で仮保存
  2. 承認者へメール通知 (承認/否認 URL 付き)
  3. 承認時: 申請者のトークンで `POST /me/events` → Outlook カレンダー登録 + 申請者へ確認メール
  4. 否認時: Firestore ステータス更新 + 申請者へ通知メール
- **Firestore トークンキャッシュ永続化**: ステートレス Cloud Run 環境でも認証セッション維持

## ローカル開発環境のセットアップ

### 1. 依存ライブラリのインストール

```bash
pip install -r requirements.txt
```

### 2. 環境変数の設定

`.env.template` をコピーして `.env` を作成し、各値を設定します。

```bash
cp .env.template .env
```

| 変数名 | 説明 |
|--------|------|
| `CLIENT_ID` | Entra ID アプリケーション (クライアント) ID |
| `CLIENT_SECRET` | Entra ID クライアントシークレット |
| `TENANT_ID` | Entra ID テナント ID (省略時 `common`) |
| `REDIRECT_URI` | OAuth リダイレクト URI (ローカル: `http://localhost:8501`) |
| `APPROVER_EMAIL` | 承認者のメールアドレス |
| `ENCRYPTION_KEY` | トークンキャッシュ暗号化キー (Fernet 32-byte base64) |
| `USE_LOCAL_MOCK_DB` | `true` でローカル SQLite 使用 (開発時推奨) |

#### 暗号化キーの生成

```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

### 3. アプリ起動

```bash
streamlit run app.py
```

ブラウザで `http://localhost:8501` を開き、Microsoft 365 アカウントでサインインします。

## Microsoft Entra ID アプリ登録の設定

Azure Portal でアプリを登録し、以下を設定してください：

- **リダイレクト URI**: `http://localhost:8501` (ローカル) / Cloud Run URL (本番)
- **API アクセス許可 (委任された権限)**:
  - `User.Read`
  - `Calendars.ReadWrite`
  - `Place.Read.All`
  - `Mail.Send`
  - `offline_access`

## Cloud Run へのデプロイ

```bash
# Docker イメージのビルドとプッシュ
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/outlook-scheduler

# Cloud Run へデプロイ
gcloud run deploy outlook-scheduler \
  --image gcr.io/YOUR_PROJECT_ID/outlook-scheduler \
  --platform managed \
  --region asia-northeast1 \
  --cpu 2 \
  --memory 4Gi \
  --no-allow-unauthenticated \
  --set-env-vars CLIENT_ID=xxx,TENANT_ID=xxx,REDIRECT_URI=https://YOUR_CLOUDRUN_URL,APPROVER_EMAIL=approver1@co.jp,approver2@co.jp \
  --set-secrets CLIENT_SECRET=client-secret:latest,ENCRYPTION_KEY=encryption-key:latest
```

> **注意**: `CLIENT_SECRET` と `ENCRYPTION_KEY` は必ず Secret Manager 経由で注入してください。環境変数への直接設定は避けてください。

### IAP（Identity-Aware Proxy）の設定

`--no-allow-unauthenticated` に設定した場合、Cloud Run への直接アクセスは拒否されます。
社内ユーザーがアクセスできるよう IAP を設定してください。

```bash
# IAP を有効化
gcloud iap web enable --resource-type=cloud-run \
  --service=outlook-scheduler \
  --region=asia-northeast1

# 社内ユーザー/グループにアクセス権を付与
gcloud iap web add-iam-policy-binding \
  --resource-type=cloud-run \
  --service=outlook-scheduler \
  --region=asia-northeast1 \
  --member="domain:yourcompany.com" \
  --role="roles/iap.httpsResourceAccessor"
```

## セキュリティ方針

- シークレット (CLIENT_SECRET 等) はソースコードに絶対にハードコードしない
- トークンキャッシュは Fernet 対称暗号で暗号化して Firestore に保存
- Datastore エンティティの大容量フィールドは `exclude_from_indexes=True` を必ず設定
- トークン切れ (`TokenExpiredException`) 発生時は即座に再ログイン画面へ誘導
