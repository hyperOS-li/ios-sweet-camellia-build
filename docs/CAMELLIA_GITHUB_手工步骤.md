# CAMELLIA XNU 构建：GitHub 手工步骤

> 你只需要浏览器（github.com 已能打开）。做完把 kernel 拿回来，剩下的打包/刷机我来。

## 0. 先完成登录

GitHub 的 2FA 页面：点 **Send a verification code via email**，去邮箱收 6 位码填进去。

## 1. 导入仓库（私有）

1. 打开 <https://github.com/new/import>；
2. **Source repository**：`https://github.com/dipa-studio/ios-sweet`；
3. **Repository name**：`ios-sweet-camellia`；
4. 选 **Private**；
5. **Begin import**，等完成（几分钟）。

> 这是“服务端导入”，不用你上传整个仓库。

## 2. 上传 CAMELLIA 补丁

1. 进入你的仓库 → `xnu/patches/`；
2. 右上 **Add file → Upload files**；
3. 选电脑上的 `0082-camellia-machine-config.patch`（在工作区 `xnu/patches/` 里）；
4. **Commit changes**。

补丁会按文件名顺序（0001…0082）在 CI 里自动应用；它加的是：

- `CAMELLIA` machine config；
- `-DARM64_BOARD_CONFIG_CAMELLIA -march=armv8.2-a+crypto`；
- 64 字节 cache line；
- 跳过 arm64e（A76/A55 没有 PAC）。

## 3. 跑构建

1. 仓库顶部 **Actions**；
2. 左侧 **xnu** → 右侧 **Run workflow**；
3. 参数：
   - `machine_config`：**CAMELLIA**
   - `kernel_config`：DEVELOPMENT（默认）
   - 其他默认；
4. **Run workflow**；
5. 等 **10–20 分钟**（macOS runner）。

## 4. 下载 kernel

1. 构建完成后点进这次 run；
2. 页面底部 **Artifacts** → 下载 **`xnu-kernel`**；
3. 解压后应该有 `kernel.development.camellia`（或类似名字）；
4. 把文件放到工作区 `payload/out/xnu/`，告诉我路径。

## 5. 如果失败

1. 点进 run → 失败的 step → 把 **红色报错** 截图/复制给我；
2. 或下载 **`xnu-buildtree`** artifact 给我；
3. 我改补丁，你再上传一次（重复第 2–3 步）。

## 6. 注意

- 私有仓库的 macOS 分钟按 **10×** 计费：免费 2,000 分钟/月 ≈ **200 macOS 分钟 ≈ 25 次构建**，省着用；
- 第一次只求“编译通过”，不指望能启动；
- 不要动 `userdata`，手机保持官方 MIUI。

---

## 7. N2b 批次（2026-10-30）：上传 0110–0114

仓库已经在跑（`hyperOS-li/ios-sweet-camellia`，`xnu/patches/` 里已有 0001–0109）。这次只要把 **5 个新补丁**放进 `xnu/patches/`，再 Run 一次：

| 文件（工作区 `红米Note10_MTK_iOS/xnu/patches/`） | 作用 |
|---|---|
| `0110-camellia-skip-secure-bsdroot.patch` | SWEET 下跳过 `IOSecureBSDRoot`（N1 起就用二进制补丁顶着，正式化） |
| `0111-camellia-sweet-ufs-lun-arg.patch` | `ufslun=` 选 LUN（默认 2 = camellia 的 sdc） |
| `0112-camellia-sweet-ufs-no-panic.patch` | UFS root 失败不再 panic；`ufshang=1` 停在诊断屏 |
| `0113-camellia-sweet-ufs-gpt-dump.patch` | 打印 GPT 摘要（nent/used/first_usable/last_usable/alt_lba/tail_free） |
| `0114-camellia-kminit-optin.patch` | `kminit=1` 才跑 `kminit()`（修 `/dev/console` 的 NULL tty） |

步骤：

1. 进仓库 → `xnu/patches/` → **Add file → Upload files** → 把这 5 个 `.patch` 一起拖进去 → **Commit changes**；
2. **Actions** → **xnu** → **Run workflow**：
   - `macos_version`：`13.5`
   - `kernel_config`：`DEVELOPMENT`
   - `machine_config`：**`CAMELLIA`**
   - `keep_going`：`true`
3. 等 **5–6 分钟**（已有缓存时）到 **10–20 分钟**；
4. 把 **这次 run 的编号**（URL 里 `/runs/` 后面那串数字）发我，我只下载 17 MB 的 kernel；也可以直接下载 `xnu-kernel` artifact 给我。

补丁有任何 `does not apply` / 编译报错，把红色日志贴我，我改。手机这边不动。`
