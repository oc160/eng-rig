# install.ps1 - asset-hub bootstrap (ASCII-only by design: no Chinese literals,
# all paths derived at runtime so the script survives PS5.1 no-BOM encoding).
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1            # install/repair
#   powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 -Verify    # health check only
#
# 2026-10-20 架构评估修正：
#   - hook 改为 shim（两行垫片直调仓库内 canonical），不再复制 -> 消灭副本漂移窗口
#   - junction 校验真实目标（原实现只看 SKILL.md 可读，旧副本/错目标会被放过）
#   - 删除旧链接用 rmdir（PS5.1 的 Remove-Item -Recurse 对 junction 有递归进目标的风险）
# 2026-11-16 Antigravity L1 bridge (ASCII on purpose - this file is no-BOM):
#   - two idempotent junctions, both -> <repo>\skills: in-repo .agents\skills
#     (Antigravity stops its upward search at the repo root, so the bridge must
#     live inside the repo) and global ~/.gemini/config/skills (parent dir is
#     auto-created; an occupied non-junction is warned about and never overwritten)
param([switch]$Verify)

$ErrorActionPreference = "Stop"
$LibRoot  = $PSScriptRoot                       # ...\cheng-guo-zheng-li (the monorepo root)
$WsSkills = Join-Path (Split-Path $LibRoot -Parent) ".agents\skills"   # workspace-level
$UsSkills = Join-Path $env:USERPROFILE ".agents\skills"                # user-level
$ClaudeSk = Join-Path $env:USERPROFILE ".claude\skills"                # Claude Code compat

# skill list derived from the directory (no hand-maintained list)
$skills = Get-ChildItem -Path (Join-Path $LibRoot "skills") -Directory |
          Where-Object { $_.Name -ne "_template" } |
          Select-Object -ExpandProperty Name

function Get-JunctionTarget($link) {
    try {
        $item = Get-Item -LiteralPath $link -Force
        if ($item.LinkType -eq "Junction" -or $item.LinkType -eq "SymbolicLink") {
            if ($item.Target) { return [IO.Path]::GetFullPath([string]$item.Target) }
        }
    } catch { }
    return $null
}

function Remove-Link($link) {
    # rmdir removes the junction itself only; Remove-Item -Recurse would walk into the target
    cmd /c rmdir "$link" | Out-Null
}

function Ensure-Junction($link, $target) {
    $want = [IO.Path]::GetFullPath($target)
    $have = Get-JunctionTarget $link
    if ($have) {
        if ($have.TrimEnd('\') -ieq $want.TrimEnd('\')) { return $true }
        Remove-Link $link                      # wrong target (stale copy / renamed repo)
    } elseif (Test-Path -LiteralPath $link) {
        Remove-Link $link
    }
    New-Item -ItemType Junction -Path $link -Target $target | Out-Null
    return (Test-Path -LiteralPath (Join-Path $link "SKILL.md"))
}

function Ensure-Bridge($tag, $link, $target) {
    # one junction linking a whole dir; idempotent; occupied non-junction -> warn & skip
    $want = [IO.Path]::GetFullPath($target)
    $have = Get-JunctionTarget $link
    if ($have) {
        if ($have.TrimEnd('\') -ieq $want.TrimEnd('\')) { Write-Host "  OK  [$tag] present: $link"; return $true }
        Remove-Link $link                       # stale target -> rebuild below
    } elseif (Test-Path -LiteralPath $link) {
        Write-Host "  !   [$tag] occupied (not a junction), skipped: $link"
        return $false
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $link -Parent) | Out-Null
    New-Item -ItemType Junction -Path $link -Target $target | Out-Null
    Write-Host "  OK  [$tag] created: $link -> $target"
    return $true
}

$fail = 0
Write-Host "== asset-hub install =="

# 1) skill junctions (workspace-level + user-level), same source = edit once, live everywhere
foreach ($base in @($WsSkills, $UsSkills)) {
    New-Item -ItemType Directory -Force -Path $base | Out-Null
    foreach ($s in $skills) {
        $link = Join-Path $base $s
        $tgt  = Join-Path $LibRoot "skills\$s"
        if ($Verify) {
            $have = Get-JunctionTarget $link
            $ok = $have -and ($have.TrimEnd('\') -ieq ([IO.Path]::GetFullPath($tgt)).TrimEnd('\'))
            if (-not $ok) { Write-Host "  !   [$($base)] $s target=$(if($have){$have}else{'<not a junction>'})" }
        } else {
            $ok = Ensure-Junction $link $tgt
        }
        $tag = if ($base -eq $WsSkills) { "workspace" } else { "user" }
        if ($ok) { Write-Host "  OK  [$tag] $s" } else { Write-Host "  FAIL [$tag] $s -> $tgt"; $fail++ }
    }
}

# 2) Claude Code compat: ~/.claude/skills -> ~/.agents/skills (only when ~/.claude exists)
if (Test-Path (Split-Path $ClaudeSk -Parent)) {
    if ($Verify) {
        $ok = (Test-Path -LiteralPath (Join-Path $ClaudeSk "pdf-stamp\SKILL.md"))
        if ($ok) { Write-Host "  OK  [claude] skills-bridge" } else { Write-Host "  FAIL [claude] skills-bridge"; $fail++ }
    } else {
        $have = Get-JunctionTarget $ClaudeSk
        if (-not $have) {
            if (Test-Path -LiteralPath $ClaudeSk) { Remove-Link $ClaudeSk }
            New-Item -ItemType Junction -Path $ClaudeSk -Target $UsSkills | Out-Null
            Write-Host "  OK  [claude] skills-bridge created"
        } else {
            Write-Host "  OK  [claude] skills-bridge present"
        }
    }
} else {
    Write-Host "  --  [claude] ~/.claude not present, skipped (rerun after installing Claude Code)"
}

# 3) Antigravity bridge: repo-local .agents\skills + global ~/.gemini\config\skills
#    (both -> <repo>\skills; see the 2026-11-16 header note for why two bridges)
$AgSkills  = Join-Path $LibRoot "skills"
$AgBridges = @(
    @("ag-local",  (Join-Path $LibRoot ".agents\skills")),
    @("ag-global", (Join-Path $env:USERPROFILE ".gemini\config\skills"))
)
foreach ($b in $AgBridges) {
    $tag  = $b[0]
    $link = $b[1]
    if ($Verify) {
        $have = Get-JunctionTarget $link
        if ($have -and ($have.TrimEnd('\') -ieq ([IO.Path]::GetFullPath($AgSkills)).TrimEnd('\'))) {
            Write-Host "  OK  [$tag] $link"
        } elseif (Test-Path -LiteralPath $link) {
            Write-Host "  --  [$tag] occupied (not a junction), skipped: $link"
        } else {
            Write-Host "  FAIL [$tag] $link"; $fail++
        }
    } else {
        $null = Ensure-Bridge $tag $link $AgSkills   # occupied path is a warning, not a failure
    }
}

# 4) git: hub remote + pre-commit/pre-push shims (shim, not a copy: canonical hook may change freely)
$hookDst  = Join-Path $LibRoot ".git\hooks\pre-commit"
$pushDst  = Join-Path $LibRoot ".git\hooks\pre-push"
$shim = @"
#!/bin/bash
# asset-hub hook shim -- always runs the canonical hook inside the repo (no copy, no drift)
ROOT=`$(git rev-parse --show-toplevel) || exit 0
exec bash "`$ROOT/_meta/hooks/pre-commit" "`$@"
"@
$pushShim = @"
#!/bin/bash
# asset-hub pre-push shim -- runs core smoke subset on non-tasks branches
ROOT=`$(git rev-parse --show-toplevel) || exit 0
exec bash "`$ROOT/_meta/hooks/pre-push" "`$@"
"@
if (Test-Path (Join-Path $LibRoot ".git")) {
    if (-not $Verify) {
        if (-not (git -C $LibRoot remote | Select-String -Quiet "hub")) {
            git -C $LibRoot remote add hub "ssh://git@your-git-server/repos/asset-hub.git"
            Write-Host "  OK  [git] remote hub added"
        }
        [IO.File]::WriteAllText($hookDst, $shim.Replace("`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))
        [IO.File]::WriteAllText($pushDst, $pushShim.Replace("`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "  OK  [git] pre-commit + pre-push shims written"
    } else {
        $okHook = (Test-Path -LiteralPath $hookDst) -and ((Get-Content -LiteralPath $hookDst -Raw) -match "hook shim")
        $okPush = (Test-Path -LiteralPath $pushDst) -and ((Get-Content -LiteralPath $pushDst -Raw) -match "pre-push shim")
        $okRem  = (git -C $LibRoot remote | Select-String -Quiet "hub")
        if ($okHook -and $okPush -and $okRem) { Write-Host "  OK  [git] hub remote + pre-commit/pre-push shims" }
        else { Write-Host "  FAIL [git] hub/hook ($okRem/$okHook/$okPush)"; $fail++ }
    }
}

# 5) zero-phrase agent hooks (SessionStart / UserPromptSubmit / PostToolUse)
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    if (-not $Verify) {
        & $py.Source (Join-Path $LibRoot "_meta\install_hooks.py") | Out-Null
        Write-Host "  OK  [hooks] agent hooks installed"
    } else {
        Write-Host "  OK  [hooks] (run _meta\install_hooks.py to repair)"
    }
} else {
    Write-Host "  --  [hooks] python not found, skipped"
}

if ($fail -gt 0) { Write-Host "== RESULT: $fail FAILURES =="; exit 1 }
Write-Host "== RESULT: all green =="
