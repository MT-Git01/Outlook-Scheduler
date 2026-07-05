# テスト環境の動作環境

- OS: Windows 11
- 開発時のWebフレームワーク：Streamlit

# 開発時の制約
- 認証には Microsoft Entra ID を用いた「委任された権限 (Delegated permissions)」を採用する。
- ステートレス環境でのトークンキャッシュの永続化には Firestore (Datastore モード) を使用する。
- 既存のoutlookカレンダーに会議室が予約されている場合は、会議室の予約が重なる時間の候補表示は行わない。 

# 本番時の動作環境
- Google Cloud Run

# Project Context: Enterprise Outlook Scheduler with HITL Approval on GCP

## 1. 開発目的 & 概要
本プロジェクトは、GCP (Cloud Run) 上に構築される、Microsoft 365 (Outlook) と連携したエンタープライズ向けの日程調整および会議室予約アプリケーションである。
エンタープライズガバナンスとセキュリティを担保するため、アプリ固有の特権（アプリケーション許可）は使用せず、**Microsoft Entra ID を用いた「委任された権限 (Delegated permissions)」**を採用する。サインインしたユーザー自身の権限の範囲内で、参加者および会議室の空き状況を考慮したスケジュール調整を行い、管理者の承認を経て初めて予約が確定する「ヒューマン・イン・ザ・ループ (HITL)」フローを実装する。

## 2. 技術スタック
- **Frontend / Backend:** Python (Streamlit などの軽量Webフレームワーク)
- **Authentication & API:** `msal` (Microsoft Authentication Library for Python), Microsoft Graph API
- **Infrastructure:** Google Cloud Run (Docker コンテナによるステートレスデプロイ)
- **State & Data Store:** Google Cloud Firestore (Datastore モード / 承認ステートおよびトークンキャッシュの永続化)
- **Secret Management:** Secret Manager (GCP) または環境変数

---

## 3. 認証・認可アーキテクチャ (Entra ID & OAuth 2.0)

### 3.1 委任された権限とスコープ
アプリはサインインしたユーザーの権限を「委任」されて動作する。ユーザーがOutlook上で予約・閲覧権限を持たない会議室や他人のスケジュールにはアクセスできない仕様（M365側のガバナンスに準拠）とする。
- **必要とされる必須スコープ:** - `User.Read` (ユーザープロファイル取得)
  - `Calendars.ReadWrite` (カレンダーの閲覧・予定の追加・削除)
  - `Place.Read.All` (会議室一覧および収容人数の取得)
  - `Mail.Send` (承認者・申請者への通知メール送信)
  - `offline_access` (バックエンド処理用のリフレッシュトークン取得)

### 3.2 認証フロー (Authorization Code Flow)
1. ユーザーが Cloud Run アプリにアクセス。
2. アプリが `msal.ConfidentialClientApplication` を用いて、Entra ID のログイン認可URLを生成しリダイレクト。
3. ユーザーが M365 アカウントでサインインし、アクセス許可に同意。
4. Entra ID から Cloud Run（環境変数 `REDIRECT_URI` で指定された `/callback`）へ認可コードが返却される。
5. アプリはバックエンドで認可コードをアクセストークンおよびリフレッシュトークンと交換する。

---

## 4. インフラ構造とステート管理の制約 (Cloud Run 対策)
Cloud Run は完全な**ステートレス環境**であり、自動スケーリングやインスタンスの再起動が発生する。そのため、コンテナのメモリ上でのセッション管理やトークンキャッシュは禁止とする。

### 4.1 トークンキャッシュの外部化
- `msal` が提供する `SerializableTokenCache` を拡張する。
- トークンの更新（シリアライズ）が発生するたびに、**Firestore** へ、ユーザー識別子（`home_account_id` 等）をキーにして暗号化して保存・更新する。
- APIリクエスト処理の開始時に、Firestore からキャッシュをロード（デシリアライズ）して `ConfidentialClientApplication` に渡す構造を徹底すること。

---

## 5. 主要な Microsoft Graph API 連携 & 処理ロジック

AIエージェントは、以下のエンドポイントを呼び出す Python 関数をモジュール（例: `graph_client.py`）としてカプセル化して生成すること。

### 5.1 会議室一覧の取得とフィルタリング
- **エンドポイント:** `GET /places/microsoft.graph.room`
- **ロジック:** 返却される会議室（`rooms`）のリストから、UIで指定された「参加人数（主催者＋参加者）」以上の `capacity`（収容人数）を持つ会議室の `emailAddress` および `displayName` を抽出して絞り込む。

### 5.2 参加者および会議室の空き時間の一括検索 (Find Meeting Times)
- **エンドポイント:** `POST /me/findMeetingTimes`
- **ロジック:** 5.1でフィルタリングした候補会議室（最大制限数に注意）と、必須参加者のメールアドレスをペイロードの `attendees` および `locationConstraint` に含めて一括検索する。全員が共通で空いている時間枠（`meetingTimeSuggestions`）と、その枠で利用可能な会議室（`locations`）の組み合わせをUIに提示する。

### 5.3 【HITL】承認フローとステート管理
会議室の予約確定前に、管理者の承認工程を挟む非同期ワークフローを実装する。

1. **予約申請の仮保存 (Firestore):**
   ユーザーが候補日時と会議室を選択して「申請」した際、Outlook APIはまだ叩かず、Firestoreの `booking_requests` コレクションにステータス `pending` で予定データ（件名、日時、参加者、選択された会議室、申請者ユーザーID）を保存する。
2. **承認者へのメール通知:**
   Graph API (`POST /me/sendMail`) を使用し、環境変数 `APPROVER_EMAIL` で指定された承認者へ「承認依頼メール」を送信する。メール本文には、Cloud Run上の承認/否認アクションを実行するためのWeb画面URL（申請ID付き）を含める。
3. **承認時の予約確定 (Create Event):**
   - 承認者が「承認」を実行した場合、Firestoreのステータスを `approved` に更新する。
   - 同時に、**申請者（予約者）の永続化されたトークン（Firestoreからロード）**を用いて `POST /me/events` を呼び出し、Outlookカレンダーへの追加と会議室の予約を確定させる。
   - 会議室（Room）は `attendees` リストに `type: "resource"` として含め、`location` オブジェクトにも会議室情報を明示することで、Teams会議URL付きの招待を自動発行・確定させる。
4. **否認時の処理:**
   - 承認者が「否認」を実行した場合、Firestoreのステータスを `rejected` に更新し、申請者へ通知メールを送信する（Outlookへの登録は行わない）。

---

## 6. 実装フェーズのガイドライン

### Phase 1: ローカルでの認証・検索プロトタイプ
- `localhost:8501` で動作する Streamlit アプリを構築。
- `msal` を用いたログイン認証フロー、および `GET /places/microsoft.graph.room` からの会議室取得、`POST /me/findMeetingTimes` による空き時間検索までの基本ロジックを実装せよ。

### Phase 2: Firestore 永続化レイラーの実装
- MSAL のカスタムトークンキャッシュ機構を実装し、Firestore へのトークン保存・復元ロジックを確立せよ。
- 予約申請を `pending` 状態で保存する Firestore スキーマのロジックを作成せよ。

### Phase 3: HITL 承認画面と通知ロジックの結合
- 承認者向けの簡易Web画面（URLパラメータから申請IDを読み取り、承認/否認ボタンを配置）を作成せよ。
- 承認ボタン押下時に、申請者のトークンをコンテキストにロードして `POST /me/events` を実行するバックエンド処理を完成させよ。

### Phase 4: コンテナ化と GCP デプロイ
- `Dockerfile`、環境変数マッピングの設定ファイルを生成せよ。

## 7. コーディング規約 & セキュリティ方針
- `CLIENT_SECRET` などの機密情報は絶対にソースコードにハードコードせず、環境変数または Secret Manager 経由で注入すること。
- エラーハンドリングを徹底し、特にトークン切れ（`MsalUiRequiredException`）が発生した場合は、速やかに再ログイン画面へユーザーを誘導、あるいは適切なエラーメッセージを表示する設計にすること。