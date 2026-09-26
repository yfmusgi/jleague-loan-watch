# J.League Loan Player Watch

Jリーグ公式サイトの試合ページを定期確認し、登録した選手のスタメン発表を検知してDiscordへ通知する小さな無料システムです。

## 無料構成

- GitHub public repository
- GitHub Actions
- Python
- Discord Webhook
- Jリーグ公式サイト

公開リポジトリのGitHub-hosted standard runnerは無料です。

## 1. GitHubにリポジトリを作る

GitHubで `jleague-loan-watch` というPublic repositoryを作成し、このフォルダの中身をアップロードします。

## 2. 選手を登録

`config.json` の `players` を編集します。

例:

```json
{
  "players": [
    {"name": "選手A", "team": "大宮アルディージャ"},
    {"name": "選手B", "team": "Ｖ・ファーレン長崎"}
  ],
  "lookahead_days": 14,
  "check_minutes_before": 120,
  "discord_webhook_env": "DISCORD_WEBHOOK_URL"
}
```

`team` は現バージョンでは表示用/将来の厳密判定用です。スタメン判定は選手名で行います。

## 3. Discord Webhook

Discordで自分用サーバー/チャンネルを作り、チャンネル設定からWebhookを作成します。

GitHub repository:
Settings → Secrets and variables → Actions → New repository secret

- Name: `DISCORD_WEBHOOK_URL`
- Secret: Discord Webhook URL

## 4. 動作確認

GitHub:
Actions → `J.League loan player watch` → Run workflow

成功すると、スタメン発表済みの対象選手がDiscordへ通知されます。

## 注意

この初版は「先発かどうか」を判定します。
「ベンチ入り」「ベンチ外」の区別は別実装にしたほうが安全です。

また、Jリーグ公式サイトのHTML構造が変更された場合はスクレイパーの修正が必要です。
