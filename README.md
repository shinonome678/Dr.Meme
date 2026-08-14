# Dr. Meme

Discord の通常メッセージを監視し、登録済みキーワードに反応してネットミーム画像を返信する Python Bot です。

## 主な機能

- `/meme add` でキーワード、画像、判定方法を指定して登録
- 画像付きメッセージを右クリックして `Apps` → `ミームとして登録` から登録
- `partial` は部分一致、`exact` は前後空白を除いた完全一致で判定
- 複数のキーワードが一致した場合は、最も長いキーワードのミームを1件だけ返信
- 同じミームはサーバー単位で10秒の Cooldown
- SQLite にサーバー別の登録情報を保存
- Discord の添付URLだけに依存せず、画像を `data/images/` に保存
- 管理者または `ミーム編集者` ロールだけが登録、編集、削除、有効化、無効化を実行可能

## ファイル構成

```text
Dr.Meme/
├─ bot.py
├─ config.py
├─ database.py
├─ image_storage.py
├─ meme_matching.py
├─ permissions.py
├─ requirements.txt
├─ .env.example
├─ .gitignore
├─ README.md
├─ cogs/
│  ├─ __init__.py
│  ├─ meme_commands.py
│  └─ meme_listener.py
└─ data/
   └─ images/
```

## Discord 側の準備

1. Discord Developer Portal で Application を作成します。
2. `Bot` ページで Bot を作成し、Token を取得します。
3. `Bot` → `Privileged Gateway Intents` → `Message Content Intent` を ON にします。
4. `OAuth2` → `URL Generator` で Bot を招待します。
5. Scopes は `bot` と `applications.commands` を選択します。
6. Bot Permissions は最低限、次を付けます。

- View Channels
- Send Messages
- Read Message History
- Attach Files
- Use Application Commands

権限値を直接使う場合は `2147585024` です。Administrator は不要です。

## Python 側の準備

Windows + VS Code の PowerShell 例です。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env` を開き、最低限 `DISCORD_TOKEN` を入力します。

```env
DISCORD_TOKEN=ここにBot Token
TEST_GUILD_ID=
MEME_EDITOR_ROLE=ミーム編集者
MEME_COOLDOWN_SECONDS=10
ALLOW_EVERYONE_TO_EDIT=false
SYNC_GLOBAL_COMMANDS=true
```

### `.env` の設定

- `DISCORD_TOKEN`: Discord Developer Portal で取得した Bot Token
- `TEST_GUILD_ID`: 開発中のサーバーID。設定すると Slash Command と右クリックメニューの反映が速くなります。
- `MEME_EDITOR_ROLE`: 管理者以外にミーム管理を許可するロール名
- `MEME_COOLDOWN_SECONDS`: 同じミームの自動返信 Cooldown 秒数
- `ALLOW_EVERYONE_TO_EDIT`: `true` にするとサーバー全員が登録、編集、削除できます
- `SYNC_GLOBAL_COMMANDS`: `true` なら `TEST_GUILD_ID` 未設定時にグローバルコマンドを同期します

開発中は `TEST_GUILD_ID` を入れるのがおすすめです。未設定の場合、グローバルコマンドとして同期され、Discord 側への反映に時間がかかることがあります。

## 起動

```powershell
.\.venv\Scripts\Activate.ps1
python bot.py
```

初回起動時に `data/memes.db` と `data/images/` が自動作成されます。

## Discord で最初に試す操作

### Slash Command 登録

1. Discord に画像ファイルを用意します。
2. `/meme add keyword:何見てんだよ image:xxx.jpg match_type:partial` を実行します。
3. `さっきから何見てんだよ` と投稿します。
4. Bot が登録画像だけを Reply します。

### 右クリック登録

1. Discord にミーム画像を投稿します。
2. 画像付きメッセージを右クリックします。
3. `Apps` → `ミームとして登録` を選択します。
4. Modal にキーワードを入力します。
5. 判定方法は `partial` または `exact` を入力します。迷ったら `partial` のままで大丈夫です。
6. 登録後、誰かがキーワードを含むメッセージを投稿すると Bot が画像を返信します。

## コマンド

- `/meme add keyword image match_type`: ミーム登録
- `/meme delete id`: ミーム削除。DBレコードとローカル画像を削除します。
- `/meme list page`: 登録一覧を表示します。
- `/meme show id`: 詳細と画像を表示します。
- `/meme edit id keyword match_type`: キーワードや判定方法を変更します。
- `/meme enable id`: 自動返信を有効化します。
- `/meme disable id`: 自動返信を無効化します。

## 権限管理

登録、編集、削除、有効化、無効化、右クリック登録は、次のどちらかを満たすユーザーだけが実行できます。

- Discord サーバー管理者
- `.env` の `MEME_EDITOR_ROLE` と同じ名前のロールを持つメンバー

初期値は `ミーム編集者` です。サーバー側で同名ロールを作り、共同編集したい友人に付与してください。

## 画像保存とDB

- DB: `data/memes.db`
- 画像: `data/images/`
- DB には `images/<uuid>.<ext>` のような相対パスを保存します。
- 対応形式は `jpg`, `jpeg`, `png`, `webp`, `gif` です。
- 同じサーバー内で同じ `keyword` と `match_type` の組み合わせは重複登録できません。

## 注意

- Bot Token を `.env` 以外に書かないでください。
- `.env`, `data/memes.db`, `data/images/` は `.gitignore` に含めています。
- Bot が通常メッセージを読むため、Discord Developer Portal の Message Content Intent を必ず ON にしてください。
