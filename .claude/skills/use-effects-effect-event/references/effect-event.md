# useEffectEvent 詳細パターン

useEffectEventを使用してEffectを最適化する詳細パターンを提供します。

## 基本概念

useEffectEventは、Effect内で使用する関数から「リアクティブでない」部分を抽出するためのReact Hookです。

### 問題

```typescript
// ❌ 問題: userIdが変わるたびにEffectが再実行される
function PageView({ pageUrl }) {
  const userId = useAuthContext()

  useEffect(() => {
    logAnalytics(pageUrl, userId)
  }, [pageUrl, userId]) // userIdの変更でも再ログされる
}
```

### 解決

```typescript
// ✅ 解決: pageUrlが変わったときだけEffectが再実行される
function PageView({ pageUrl }) {
  const userId = useAuthContext()

  const onLog = useEffectEvent((url) => {
    logAnalytics(url, userId) // 常に最新のuserIdを使用
  })

  useEffect(() => {
    onLog(pageUrl)
  }, [pageUrl]) // userIdは依存配列に不要
}
```

## パターン1: ログと分析

### ページビュートラッキング

```typescript
function PageTracker({ page }) {
  const user = useAuthContext()
  const theme = useTheme()

  const onPageView = useEffectEvent((pageName) => {
    analytics.track('page_view', {
      page: pageName,
      userId: user?.id,
      theme: theme,
      timestamp: Date.now(),
    })
  })

  useEffect(() => {
    onPageView(page)
  }, [page]) // pageが変わったときだけトラッキング
}
```

### エラーログ

```typescript
function ErrorBoundary({ children }) {
  const user = useAuthContext()
  const version = useAppVersion()

  const onError = useEffectEvent((error) => {
    logError({
      error: error.message,
      stack: error.stack,
      userId: user?.id,
      version: version,
      timestamp: Date.now(),
    })
  })

  useEffect(() => {
    const handleError = (event) => {
      onError(event.error)
    }

    window.addEventListener('error', handleError)
    return () => window.removeEventListener('error', handleError)
  }, []) // マウント時に1回だけセットアップ
}
```

## パターン2: タイマーと遅延処理

### オートセーブ

```typescript
function AutoSaveForm({ formData }) {
  const userId = useAuthContext()
  const isOnline = useOnlineStatus()

  const onSave = useEffectEvent(() => {
    if (isOnline) {
      saveFormData({
        ...formData,
        userId: userId,
        savedAt: Date.now(),
      })
    }
  })

  useEffect(() => {
    const timer = setInterval(() => {
      onSave() // 常に最新のisOnlineとuserIdを使用
    }, 5000)

    return () => clearInterval(timer)
  }, [formData]) // formDataが変わったときだけタイマーをリセット
}
```

### デバウンス検索

```typescript
function SearchInput({ query }) {
  const filters = useFilters()
  const userId = useAuthContext()

  const onSearch = useEffectEvent((searchQuery) => {
    searchAPI({
      query: searchQuery,
      filters: filters, // 常に最新のfilters
      userId: userId, // 常に最新のuserId
    })
  })

  useEffect(() => {
    const timer = setTimeout(() => {
      onSearch(query)
    }, 500)

    return () => clearTimeout(timer)
  }, [query]) // queryが変わったときだけデバウンス
}
```

## パターン3: イベントハンドラ

### WebSocket接続

```typescript
function ChatRoom({ roomId }) {
  const user = useAuthContext()
  const theme = useTheme()

  const onMessage = useEffectEvent((message) => {
    displayMessage({
      ...message,
      theme: theme, // 常に最新のtheme
      recipientId: user.id, // 常に最新のuserId
    })
  })

  useEffect(() => {
    const ws = new WebSocket(`/chat/${roomId}`)

    ws.onmessage = (event) => {
      onMessage(JSON.parse(event.data))
    }

    return () => ws.close()
  }, [roomId]) // roomIdが変わったときだけ再接続
}
```

### ファイルアップロード

```typescript
function FileUploader({ file }) {
  const user = useAuthContext()
  const uploadConfig = useUploadConfig()

  const onProgress = useEffectEvent((progress) => {
    updateUI({
      progress: progress,
      userId: user.id,
      config: uploadConfig, // 常に最新の設定
    })
  })

  useEffect(() => {
    if (!file) return

    const uploader = new Uploader(file)

    uploader.onProgress = (p) => {
      onProgress(p)
    }

    uploader.start()

    return () => uploader.cancel()
  }, [file]) // fileが変わったときだけアップロード開始
}
```

## パターン4: 複雑なEffect

### 認証フロー

```typescript
function AuthManager() {
  const navigate = useNavigate()
  const settings = useSettings()

  // 孫関数は通常の関数
  const saveToken = async (user) => {
    await localStorage.setItem('token', user.token)
  }

  const syncUserData = async (user) => {
    await api.syncUser(user.id)
  }

  const redirectUser = (user) => {
    const destination = settings.defaultRoute || '/dashboard'
    navigate(destination)
  }

  // Effectから直接呼ばれる関数のみuseEffectEvent
  const handleAuthChange = useEffectEvent(async (user) => {
    if (user) {
      await saveToken(user)
      await syncUserData(user)
      redirectUser(user) // 常に最新のsettingsとnavigateを使用
    }
  })

  useEffect(() => {
    const unsubscribe = auth.onAuthStateChanged((user) => {
      handleAuthChange(user)
    })

    return unsubscribe
  }, []) // マウント時に1回だけセットアップ
}
```

## 移行ガイド

### Before: 依存配列が肥大化

```typescript
// ❌ 問題: 依存配列に多くの値が必要
function Component({ id }) {
  const user = useUser()
  const theme = useTheme()
  const settings = useSettings()
  const analytics = useAnalytics()

  useEffect(() => {
    fetchData(id, user, theme, settings)
    analytics.track('data_fetch', { id, userId: user.id })
  }, [id, user, theme, settings, analytics]) // すべての変更でEffect再実行
}
```

### After: useEffectEventで最適化

```typescript
// ✅ 解決: 必要な値だけ依存配列に
function Component({ id }) {
  const user = useUser()
  const theme = useTheme()
  const settings = useSettings()
  const analytics = useAnalytics()

  const onFetch = useEffectEvent(() => {
    fetchData(id, user, theme, settings) // 常に最新の値を使用
    analytics.track('data_fetch', { id, userId: user.id })
  })

  useEffect(() => {
    onFetch()
  }, [id]) // idが変わったときだけEffect再実行
}
```

## ベストプラクティス

### ✅ すべきこと

1. **Effect内から直接呼ばれる関数に使用**

   ```typescript
   const onEvent = useEffectEvent(() => {...})
   useEffect(() => {
     onEvent()  // ✅ 直接呼び出し
   }, [])
   ```

2. **常に最新の値が必要な場合に使用**

   ```typescript
   const onLog = useEffectEvent((data) => {
     log(data, userId) // ✅ 常に最新のuserId
   })
   ```

3. **依存配列を最小化**
   ```typescript
   useEffect(() => {
     onEvent()
   }, [trigger]) // ✅ triggerのみ
   ```

### ❌ すべきでないこと

1. **孫関数にuseEffectEventを使用しない**

   ```typescript
   // ❌ 不要
   const helper = useEffectEvent(() => {...})
   const onEvent = useEffectEvent(() => {
     helper()  // Effect Eventの中
   })
   ```

2. **他のコンポーネントに渡さない**

   ```typescript
   // ❌ 禁止
   const onEvent = useEffectEvent(() => {...})
   return <Child onClick={onEvent} />
   ```

3. **Effect外で呼び出さない**
   ```typescript
   // ❌ 禁止
   const onEvent = useEffectEvent(() => {...})
   const handleClick = () => {
     onEvent()  // Effect外
   }
   ```

## トラブルシューティング

### Q: useEffectEventが見つからない

A: React 19.0.0以降が必要です。バージョンを確認してください。

```bash
npm list react
```

### Q: Lintエラーが出る

A: ESLintルールを更新してuseEffectEventをサポートしてください。

```json
{
  "rules": {
    "react-hooks/exhaustive-deps": [
      "warn",
      {
        "additionalHooks": "useEffectEvent"
      }
    ]
  }
}
```

### Q: いつuseEffectEventを使うべきか

A: 以下の場合に使用してください：

- 依存配列に含めたくない値がある
- 常に最新の値を使用したい
- Effectの再実行を減らしたい

### Q: useCallbackとの違いは

A:

- **useCallback**: メモ化された関数、依存配列の値が変わると新しい関数を返す
- **useEffectEvent**: 常に同じ関数参照、内部で常に最新の値を使用

## まとめ

useEffectEventは以下の場合に有効です：

1. **ログと分析**: すべての状態変更で再ログしたくない
2. **タイマー**: 常に最新の値を使用したい
3. **イベントハンドラ**: Effectのセットアップを最小化
4. **複雑なEffect**: 依存配列を簡潔に保つ

**原則**: Effect内から直接呼ばれる関数のみuseEffectEventを使用し、孫関数は通常の関数にする。
