# S.E.N.S.E. — Discord 第二の脳 Bot

DiscordをZettelkasten × GTDで運用する個人知識管理システム。  
メモ・リンクをDiscordに投げると、Claudeが蓄積された個人知識を参照しながら処理・要約し、GitHub private リポジトリに自動保存する。

---

## 技術スタック

| カテゴリ | 採用 |
|---|---|
| Bot | Python 3.9+ / discord.py 2.x |
| AI（メイン） | Claude Sonnet 4.6 |
| AI（タグ付け） | Claude Haiku 4.5 |
| 記事取得 | trafilatura + requests |
| YouTube・動画 | youtube-transcript-api → yt-dlp + faster-whisper (small) |
| 知識DB | ChromaDB + paraphrase-multilingual-mpnet-base-v2 |
| 知識ストア | GitHub private repo (PyGitHub) |

---

## ディレクトリ構成

```
discord-second-brain/        ← Botコード（このリポジトリ）
├── bot.py                   ← エントリーポイント
├── config.py                ← 環境変数・GitHub _config/ キャッシュ
├── requirements.txt
├── .env                     ← 環境変数（Git管理外）
├── cookies.txt              ← ブラウザCookie（Git管理外）
├── chroma_db/               ← ChromaDBローカルDB（Git管理外）
├── handlers/
│   ├── memo.py              ← /memo コマンド
│   └── link.py              ← /link コマンド
├── services/
│   ├── claude_client.py     ← Anthropic SDK（プロンプトキャッシュ対応）
│   ├── github_client.py     ← PyGitHub（ファイルCRUD・自動コミット）
│   ├── knowledge_store.py   ← ChromaDB（セマンティック検索）
│   ├── scraper.py           ← 記事取得（ペイウォール・複数ページ対応）
│   └── youtube_client.py    ← 動画文字起こし（YouTube・NewsPicks等）
├── session/
│   └── manager.py           ← チャンネル別セッション管理
└── utils/
    └── formatters.py        ← ZK-ID生成・テンプレート埋め込み
```

```
second-brain/                ← GitHub private repo（知識ストア）
├── 00-inbox/                ← /memo 投下直後（生テキスト）
├── 10-notes/
│   ├── fleeting/            ← /memo → Claude整理後
│   ├── literature/
│   │   ├── articles/        ← /link（記事）
│   │   └── youtube/         ← /link（YouTube・動画）
│   └── permanent/           ← [🌟 Permanent化] で生成
├── 20-research/             ← Phase 2
├── 30-planning/             ← Phase 2
├── 40-journal/              ← Phase 3
├── 50-review/               ← Phase 3
├── _config/
│   ├── system-prompt.md     ← Claudeの基本人格（GitHub上で編集可）
│   └── prompts/
│       ├── memo.md          ← /memo の処理指示
│       └── link.md          ← /link の処理指示
└── _templates/
    ├── fleeting-note.md
    ├── literature-article.md
    ├── literature-youtube.md
    └── permanent-note.md
```

---

## セットアップ

### 1. 依存インストール

```bash
pip3 install -r requirements.txt
brew install ffmpeg   # 動画音声変換に必要
```

### 2. 環境変数設定

```bash
cp .env.example .env
```

`.env` を編集：

```
DISCORD_TOKEN=          # Discord Developer Portal のBot Token
DISCORD_GUILD_ID=       # DiscordサーバーのID（開発者モードで右クリック→コピー）
ANTHROPIC_API_KEY=      # console.anthropic.com
GITHUB_TOKEN=           # GitHub → Settings → Developer settings → Personal access tokens（repo権限）
GITHUB_REPO=username/second-brain
CHROMA_DB_PATH=./chroma_db
COOKIES_FILE=./cookies.txt   # ペイウォール・有料動画対応（任意）
```

### 3. second-brain リポジトリの初期化

GitHubで `second-brain` という名前のprivateリポジトリを作成後：

```bash
cd second-brain-init
git init
git add .
git commit -m "init"
git remote add origin https://github.com/YOUR_USERNAME/second-brain
git push -u origin main
```

### 4. cookies.txt（任意・ペイウォール対応）

ChromeでYouTube・NewsPicks等にログインした状態で  
[Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) 拡張機能から **Export All Cookies** でエクスポートし、`discord-second-brain/cookies.txt` に配置。

### 5. Botの招待

Discord Developer Portal → OAuth2 → URL Generator で以下を選択：
- Scopes: `bot` + `applications.commands`
- Bot Permissions: `Send Messages` / `Embed Links` / `Read Message History` / `Use Slash Commands`

生成したURLでサーバーに招待。

### 6. 起動

```bash
cd discord-second-brain
python3 bot.py
```

起動成功時のログ：
```
Synced 2 slash commands to guild ...
Logged in as S.E.N.S.E.#1069
GitHub: connected to username/second-brain
ChromaDB: ready (N notes indexed)
```

---

## スラッシュコマンド（Phase 1）

### `/memo [text]`

メモ・アイデアをキャプチャしてClaudeが整理する。

**フロー：**
1. `00-inbox/` に生テキストを即時保存
2. ChromaDB で関連ノートをセマンティック検索（top 3）
3. Claude が整理・構造化（関連ノートを参照）
4. Discord に結果表示 + ボタン

**ボタン：**
- `💾 保存` → `10-notes/fleeting/ZK-YYYYMMDD-HHMMSS.md` にGitHubコミット + ChromaDB追加
- `🌟 Permanent化` → 保存後に出現。セッションから原子的アイデアを抽出して `10-notes/permanent/` に保存
- `❌ 破棄` → inbox削除・セッション破棄

---

### `/link [url]`

記事・YouTube・動画URLを要約して保存する。

**対応コンテンツ：**

| URL | 処理 | 保存先 |
|---|---|---|
| 一般記事 | trafilatura でテキスト取得 | `literature/articles/` |
| YouTube | 字幕取得 → なければ faster-whisper | `literature/youtube/` |
| NewsPicks動画 | yt-dlp + faster-whisper | `literature/youtube/` |
| NewsPicks記事 | trafilatura でテキスト取得 | `literature/articles/` |
| ペイウォール記事 | cookies.txt でログイン状態取得 | `literature/articles/` |
| 複数ページ記事 | 全ページ自動取得・結合（最大8ページ） | `literature/articles/` |

**ペイウォール取得失敗時：**
```
⚠️ 記事を取得できませんでした。
[✅ タイトル・URLのみ保存] [❌ スキップ]
```

**フロー：**
1. URL種別を判定（YouTube・NewsPicks動画・記事）
2. コンテンツ取得（字幕 / 文字起こし / スクレイピング）
3. ChromaDB で関連ノートをセマンティック検索
4. Claude が要約・タグ付け（関連ノートを参照）
5. Discord に結果表示 + ボタン

**ボタン：**
- `💾 保存` → `10-notes/literature/` にGitHubコミット + ChromaDB追加
- `🌟 Permanent化` → 保存後に出現
- `❌ 破棄` → セッション破棄

---

## 知識参照フロー（コアコンセプト）

```
コマンド実行
  ↓
ChromaDB でセマンティック検索（関連ノート top 3〜5件）
  ↓
Claudeのコンテキストに注入：
  [system-prompt] + [prompts/{command}.md] + [関連ノート本文] + [入力内容]
  ↓
Claude が「蓄積知識 + 一般知識」を統合して応答
  ↓
応答に参照ノートID を明示（[[ZK-YYYYMMDD-HHMMSS]] 形式）
```

---

## セッション管理

各チャンネルは独立したセッションを保持。5チャンネルで同時に異なるコマンドを実行可能。

```
#line-001  /memo 実行中
#line-002  /link（YouTube処理中）
#line-003  /link（記事取得中）  ← 互いに干渉しない
```

セッションの保存スコープ：`/command 実行` 〜 `[💾 保存]` ボタン押下まで

---

## _config/ の編集（GitHubから直接）

`second-brain/_config/` のファイルをGitHub上で編集するだけでClaudeの振る舞いが変わる。**再起動不要**（TTL 5分でキャッシュが自動更新）。

| ファイル | 効果 |
|---|---|
| `system-prompt.md` | Claudeの基本人格・応答スタイル |
| `prompts/memo.md` | /memo の処理指示・出力フォーマット |
| `prompts/link.md` | /link の要約指示・出力フォーマット |

---

## 注意事項

- `chroma_db/` `.env` `cookies.txt` は `.gitignore` 済み（Git管理外）
- faster-whisper `small` モデル（~460MB）は初回起動時に自動ダウンロード
- sentence-transformers モデル（~400MB）は初回起動時に自動ダウンロード
- 長時間動画（60分以上）はfaster-whisperの処理に20〜30分かかる
- Discord slash commandはGuild IDベースで登録（起動時に自動同期）

---

## Phase 2 予定

- `/research` — Tavily Web検索 + 蓄積知識参照
- `/planning` — 企画壁打ち
- `/search` — 過去ノート全文・セマンティック検索
- Koyeb Docker デプロイ（常時稼働）
