---
name: use-conditional-rendering-activity
description: UI表示切り替えのベストプラクティス。Activityでコンポーネントの条件分岐を最適化。タブ、モーダル、表示/非表示切替、「条件分岐UI」で && や 三項演算子の代わりに使用。
---

# Activity による条件付きレンダリングの最適化

**条件付きレンダリングは原則として Activity コンポーネントを使用してください。**

## ⚠️ 重要ルール

**条件付きレンダリングは原則Activityを使用してください。ただし、例外があります。**

### 原則（Activity使用）

- `<Activity mode={condition ? 'visible' : 'hidden'}><Component /></Activity>`

### 禁止

- `{condition && <Component />}`
- `{condition ? <A /> : <B />}`

### 例外

1. **early return**（型narrowingが必要な場合）

   ```tsx
   if (!data) return <Empty />
   // ここでdataは確定
   ```

2. **継続的な副作用の維持が必要な場合**（カメラストリームなど）

   ```tsx
   // ✅ OK: MediaStreamを維持したい場合
   <div className={isVisible ? 'block' : 'hidden'}>
     <CameraCapture />
   </div>
   ```

3. **一時停止できないリソース**
   - `MediaStream`（カメラ、マイク）: `stop()`が不可逆
   - その他、停止すると再開に大きなコストがかかるリソース

## クイックスタート

### パターン 1: タブUI

```typescript
import { Activity } from 'react'
import { useState } from 'react'

export function TabPanel() {
  const [activeTab, setActiveTab] = useState('profile')

  return (
    <div>
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="profile">プロフィール</TabsTrigger>
          <TabsTrigger value="settings">設定</TabsTrigger>
        </TabsList>

        <div className="mt-4">
          <Activity mode={activeTab === 'profile' ? 'visible' : 'hidden'}>
            <ProfileTab />
          </Activity>
          <Activity mode={activeTab === 'settings' ? 'visible' : 'hidden'}>
            <SettingsTab />
          </Activity>
        </div>
      </Tabs>
    </div>
  )
}
```

### パターン 2: モーダル/ダイアログ

```typescript
import { Activity } from 'react'

export function UserDialog({ open, onOpenChange, userId }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <Activity mode={open ? 'visible' : 'hidden'}>
          <UserDetail userId={userId} />
        </Activity>
      </DialogContent>
    </Dialog>
  )
}
```

## いつ使うか

**原則として使用する場合：**

- タブの構築（切り替え時にフォーム状態を保持）
- モーダルの構築（スクロール位置を保持）
- APIレスポンス状態の処理（loading、error、empty、data）
- 任意の条件付き表示/非表示UI

**例外：**

- 型narrowingが必要なearly returnパターン
- カメラストリーム（`MediaStream`）など、一時停止できないリソース

## 重要ルール

- `react`からインポート: `import { Activity } from 'react'`
- 表示には `<Activity mode="visible">` を使用
- 非表示には `<Activity mode="hidden">` を使用
- 非表示時もコンポーネント状態は保持される
- **⚠️ 非表示時にEffectのクリーンアップが実行される**（概念的にはアンマウント扱い）
- 条件付きレンダリングよりパフォーマンスが良い
- プリレンダリングと選択的ハイドレーションを可能にする

### Effectのクリーンアップについて

Activityで`mode="hidden"`にすると、**Effectのクリーンアップ関数が実行されます**。これは公式の仕様です：

> "概念的には、「非表示」の Activity はアンマウントされているものとして考えるべきです"

- ✅ React状態は保持される
- ✅ DOMノードは保持される（`display: none`が適用される）
- ✅ useEffect/useLayoutEffectのクリーンアップが実行される
- ✅ `visible`に戻るとEffectが再実行される

**`<video>`、`<audio>`の場合（公式推奨）**:

```tsx
// ✅ OK: Activityを使い、useLayoutEffectで一時停止
function Video() {
  const ref = useRef<HTMLVideoElement>(null)

  useLayoutEffect(() => {
    return () => {
      // Activity hidden時に一時停止
      ref.current?.pause()
    }
  }, [])

  return <video ref={ref} controls src="..." />
}

;<Activity mode={activeTab === 'video' ? 'visible' : 'hidden'}>
  <Video />
</Activity>
```

**カメラストリーム（`MediaStream`）の場合（特殊ケース）**:

カメラストリームには「一時停止」の概念がなく、`stop()`すると完全に停止し、再起動にユーザー権限が必要です。

```tsx
// ❌ NG: Activityだと、hidden時にstop()が呼ばれ、visible時に再起動が必要
<Activity mode={step === 'capture' ? 'visible' : 'hidden'}>
  <CameraCapture />
</Activity>

// ✅ OK: hidden classで非表示（ストリームを継続）
<div className={step === 'capture' ? 'block' : 'hidden'}>
  <CameraCapture />
</div>
```

**理由**: `MediaStream.stop()`は不可逆で、再開するには`getUserMedia()`を再度呼ぶ必要があり、UX低下につながる。

### ⚠️ Activity 内での型安全性

Activity は mode='hidden' でも DOM を保持するため、non-null assertion 演算子は使用禁止です。

```tsx
// ❌ NG: mode='hidden' 時に undefined で実行時エラー
<Activity mode={user ? 'visible' : 'hidden'}>
  <div>{user.email}</div>
  <Badge>{getRoleLabel(user.role)}</Badge>
</Activity>

// ✅ OK: optional chaining を使用
<Activity mode={user ? 'visible' : 'hidden'}>
  <div>{user?.email}</div>
  <Badge>{user?.role && getRoleLabel(user.role)}</Badge>
</Activity>

// ❌ NG: 子の中で && を使ってガード（stateも保持されない）
<Activity mode={user ? 'visible' : 'hidden'}>
  {user && <UserProfile user={user} />}
</Activity>
```

### Activity のネスト禁止

**Activity コンポーネントはネストせず、二次的な条件は `&&` 演算子を使用してください。**

## メリット

1. **状態保持**: コンポーネント状態（スクロール位置、フォーム入力、展開状態）が非表示時も保持される
2. **型安全**: ほとんどの場合で型narrowingが不要
3. **パフォーマンス**: コンポーネントのマウント/アンマウントより高速
4. **一貫性**: すべての条件付きレンダリングが同じパターンを使用
5. **可読性**: 並列的な状態記述はネストした条件分岐より理解しやすい
6. **リソース管理**: 非表示時にEffectがクリーンアップされるため、不要なリソースが解放される

## 使い分けガイド

### Activityを使うべきケース（基本はActivity）

- フォーム入力を保持したいタブUI
- スクロール位置を保持したいモーダル
- `<video>`、`<audio>`の再生（一時停止で十分）
- 副作用のクリーンアップが必要な場合（タイマー、購読など）

### hidden classを使うべきケース（例外）

- **カメラストリーム（`MediaStream`）**: `stop()`が不可逆で、再起動にユーザー操作が必要
- その他、「一時停止」の概念がなく、完全に停止すると再開が困難なリソース

## よくある間違い

### ❌ && 演算子を使わない

```typescript
// ❌ NG
{userName && <UserLink userName={userName} />}
{isLoading && <Spinner />}
```

### ❌ 三項演算子を表示/非表示に使わない

```typescript
// ❌ NG
{isLoading ? <Spinner /> : <UserList />}
```

### ❌ CSS displayを使わない

```typescript
// ❌ NG
<div style={{ display: isLoading ? 'block' : 'none' }}>
  <Spinner />
</div>
```

### ✅ Activityを使う

```typescript
// ✅ OK
<Activity mode={userName ? 'visible' : 'hidden'}>
  <UserLink userName={userName} />
</Activity>

<Activity mode={isLoading ? 'visible' : 'hidden'}>
  <Spinner />
</Activity>
```

## 関連スキル

- handle-edit-pages: 編集ページでloading/errorステート処理にActivityが必要
- handle-forms-rhf-zod: フォームの送信中/成功/エラー状態にActivityが必要
- manage-swr-data: データ取得・変更のloading/success/errorフィードバックにActivityが必要
- convert-enum-labels: Activity 内で enum ラベルを表示する際の optional chaining パターン
- format-dates: Activity 内で日付をフォーマットする際の optional chaining パターン
