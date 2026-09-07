# Personal Jarvis — in-sandbox screen runner (zero dependencies).
#
# A fresh Windows Sandbox guest has no Python and no package manager, and it is
# thrown away on every stop, so anything we install would have to be installed
# again on the next boot. This runner therefore uses only what every Windows
# image already ships: PowerShell plus the .NET Framework that comes with it.
#
# It is the guest half of jarvis/agent_screen/wire.py and speaks the MAILBOX
# transport: request/response files in a folder the host mapped in. The guest
# sits behind the sandbox NAT, so the host cannot dial into it — and using
# files instead means the sandbox can run with networking switched off
# entirely, which makes an agent screen a smaller attack surface than the
# user's own desktop rather than a larger one.
#
# The one ordering rule shared with the Python runner: write the binary
# payload FIRST and the response JSON LAST, so the JSON's existence proves the
# whole answer is on disk.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ConfigPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

$Native = @"
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.Text;

public static class JarvisScreen
{
    // --- input ------------------------------------------------------------
    // The struct layout below is load-bearing. On x64 an INPUT is 40 bytes
    // ONLY when the union declares MOUSEINPUT (the largest member); a smaller
    // declaration makes SendInput reject every event silently, which on the
    // host side once produced a mission that "typed" without a single
    // character appearing. Same trap, same fix, restated here because this is
    // a separate implementation.
    [StructLayout(LayoutKind.Sequential)]
    public struct MOUSEINPUT { public int dx; public int dy; public uint mouseData; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)]
    public struct KEYBDINPUT { public ushort wVk; public ushort wScan; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)]
    public struct HARDWAREINPUT { public uint uMsg; public ushort wParamL; public ushort wParamH; }
    [StructLayout(LayoutKind.Explicit)]
    public struct InputUnion
    {
        [FieldOffset(0)] public MOUSEINPUT mi;
        [FieldOffset(0)] public KEYBDINPUT ki;
        [FieldOffset(0)] public HARDWAREINPUT hi;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT { public uint type; public InputUnion U; }

    const uint INPUT_MOUSE = 0;
    const uint INPUT_KEYBOARD = 1;
    const uint MOUSEEVENTF_MOVE = 0x0001;
    const uint MOUSEEVENTF_ABSOLUTE = 0x8000;
    const uint MOUSEEVENTF_VIRTUALDESK = 0x4000;
    const uint MOUSEEVENTF_WHEEL = 0x0800;
    const uint MOUSEEVENTF_HWHEEL = 0x01000;
    const uint KEYEVENTF_KEYUP = 0x0002;
    const uint KEYEVENTF_UNICODE = 0x0004;
    const uint KEYEVENTF_EXTENDEDKEY = 0x0001;

    [DllImport("user32.dll", SetLastError = true)]
    static extern uint SendInput(uint nInputs, [In] INPUT[] pInputs, int cbSize);
    [DllImport("user32.dll")] static extern bool GetCursorPos(out POINT p);
    [DllImport("user32.dll")] static extern int GetSystemMetrics(int n);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr v);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern IntPtr FindWindowW(string c, string w);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr lp);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);

    public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; }
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

    const int SM_XVIRTUALSCREEN = 76, SM_YVIRTUALSCREEN = 77;
    const int SM_CXVIRTUALSCREEN = 78, SM_CYVIRTUALSCREEN = 79;

    public static void DeclareDpiAware()
    {
        try { if (SetProcessDpiAwarenessContext(new IntPtr(-4))) return; } catch { }
        try { SetProcessDPIAware(); } catch { }
    }

    public static int[] VirtualScreen()
    {
        return new int[] {
            GetSystemMetrics(SM_XVIRTUALSCREEN), GetSystemMetrics(SM_YVIRTUALSCREEN),
            GetSystemMetrics(SM_CXVIRTUALSCREEN), GetSystemMetrics(SM_CYVIRTUALSCREEN)
        };
    }

    public static int[] CursorPos()
    {
        POINT p; if (!GetCursorPos(out p)) return null;
        return new int[] { p.X, p.Y };
    }

    static void Send(INPUT[] inputs)
    {
        uint sent = SendInput((uint)inputs.Length, inputs, Marshal.SizeOf(typeof(INPUT)));
        if (sent != (uint)inputs.Length)
            throw new Exception("SendInput accepted " + sent + " of " + inputs.Length + " events (error " + Marshal.GetLastWin32Error() + ")");
    }

    // Absolute positioning over the whole virtual desktop, never SetCursorPos:
    // SetCursorPos misplaces onto monitors with a negative origin.
    public static void MoveAbsolute(int x, int y)
    {
        int[] v = VirtualScreen();
        int w = Math.Max(1, v[2] - 1), h = Math.Max(1, v[3] - 1);
        int nx = (int)Math.Round((double)(x - v[0]) * 65535.0 / w);
        int ny = (int)Math.Round((double)(y - v[1]) * 65535.0 / h);
        INPUT[] input = new INPUT[1];
        input[0].type = INPUT_MOUSE;
        input[0].U.mi.dx = nx; input[0].U.mi.dy = ny;
        input[0].U.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK;
        Send(input);
    }

    static void ButtonFlags(string button, out uint down, out uint up)
    {
        switch (button)
        {
            case "right": down = 0x0008; up = 0x0010; break;
            case "middle": down = 0x0020; up = 0x0040; break;
            default: down = 0x0002; up = 0x0004; break;
        }
    }

    public static void ClickAtCursor(string button, bool doubleClick)
    {
        uint d, u; ButtonFlags(button, out d, out u);
        int n = doubleClick ? 4 : 2;
        INPUT[] input = new INPUT[n];
        for (int i = 0; i < n; i++)
        {
            input[i].type = INPUT_MOUSE;
            input[i].U.mi.dwFlags = (i % 2 == 0) ? d : u;
        }
        Send(input);
    }

    public static void ButtonDown(string button) { uint d, u; ButtonFlags(button, out d, out u); INPUT[] i = new INPUT[1]; i[0].type = INPUT_MOUSE; i[0].U.mi.dwFlags = d; Send(i); }
    public static void ButtonUp(string button) { uint d, u; ButtonFlags(button, out d, out u); INPUT[] i = new INPUT[1]; i[0].type = INPUT_MOUSE; i[0].U.mi.dwFlags = u; Send(i); }

    public static void Wheel(int notches, bool horizontal)
    {
        INPUT[] input = new INPUT[1];
        input[0].type = INPUT_MOUSE;
        input[0].U.mi.mouseData = unchecked((uint)(notches * 120));
        input[0].U.mi.dwFlags = horizontal ? MOUSEEVENTF_HWHEEL : MOUSEEVENTF_WHEEL;
        Send(input);
    }

    // Unicode typing. A synthetic U+000A types nothing in a Windows edit
    // control, so newline/tab/return fall back to their virtual keys.
    public static void TypeText(string text)
    {
        foreach (char ch in text)
        {
            if (ch == '\n' || ch == '\r') { TapVk(0x0D, false); continue; }
            if (ch == '\t') { TapVk(0x09, false); continue; }
            INPUT[] input = new INPUT[2];
            for (int i = 0; i < 2; i++)
            {
                input[i].type = INPUT_KEYBOARD;
                input[i].U.ki.wScan = ch;
                input[i].U.ki.dwFlags = KEYEVENTF_UNICODE | (i == 1 ? KEYEVENTF_KEYUP : 0);
            }
            Send(input);
        }
    }

    public static void TapVk(ushort vk, bool extended)
    {
        INPUT[] input = new INPUT[2];
        for (int i = 0; i < 2; i++)
        {
            input[i].type = INPUT_KEYBOARD;
            input[i].U.ki.wVk = vk;
            input[i].U.ki.dwFlags = (extended ? KEYEVENTF_EXTENDEDKEY : 0) | (i == 1 ? KEYEVENTF_KEYUP : 0);
        }
        Send(input);
    }

    public static void KeyDown(ushort vk, bool extended)
    {
        INPUT[] input = new INPUT[1];
        input[0].type = INPUT_KEYBOARD; input[0].U.ki.wVk = vk;
        input[0].U.ki.dwFlags = extended ? KEYEVENTF_EXTENDEDKEY : 0;
        Send(input);
    }

    public static void KeyUp(ushort vk, bool extended)
    {
        INPUT[] input = new INPUT[1];
        input[0].type = INPUT_KEYBOARD; input[0].U.ki.wVk = vk;
        input[0].U.ki.dwFlags = (extended ? KEYEVENTF_EXTENDEDKEY : 0) | KEYEVENTF_KEYUP;
        Send(input);
    }

    // --- perception -------------------------------------------------------

    public static object[] Foreground()
    {
        IntPtr h = GetForegroundWindow();
        if (h == IntPtr.Zero) return null;
        StringBuilder sb = new StringBuilder(512);
        GetWindowTextW(h, sb, sb.Capacity);
        RECT r;
        if (!GetWindowRect(h, out r)) return null;
        return new object[] { h.ToInt64(), sb.ToString(), r.Left, r.Top, r.Right - r.Left, r.Bottom - r.Top };
    }

    public static bool FocusWindowByTitle(string needle)
    {
        IntPtr found = IntPtr.Zero;
        string lower = needle.ToLowerInvariant();
        EnumWindows(delegate (IntPtr hWnd, IntPtr lp)
        {
            if (!IsWindowVisible(hWnd)) return true;
            StringBuilder sb = new StringBuilder(512);
            GetWindowTextW(hWnd, sb, sb.Capacity);
            string title = sb.ToString();
            if (title.Length > 0 && title.ToLowerInvariant().Contains(lower)) { found = hWnd; return false; }
            return true;
        }, IntPtr.Zero);
        if (found == IntPtr.Zero) return false;
        ShowWindow(found, 9); // SW_RESTORE
        return SetForegroundWindow(found);
    }

    // Capture straight into the BGRX layout the host's decoder expects, so no
    // conversion pass happens anywhere in the chain.
    public static byte[] Grab(int left, int top, int width, int height, bool asRgb, out int outW, out int outH)
    {
        outW = width; outH = height;
        using (Bitmap bmp = new Bitmap(width, height, PixelFormat.Format32bppRgb))
        {
            using (Graphics g = Graphics.FromImage(bmp))
            {
                g.CopyFromScreen(left, top, 0, 0, new Size(width, height), CopyPixelOperation.SourceCopy);
            }
            BitmapData data = bmp.LockBits(new Rectangle(0, 0, width, height), ImageLockMode.ReadOnly, PixelFormat.Format32bppRgb);
            try
            {
                int rowBytes = width * 4;
                byte[] raw = new byte[rowBytes * height];
                for (int y = 0; y < height; y++)
                {
                    IntPtr rowStart = new IntPtr(data.Scan0.ToInt64() + (long)y * data.Stride);
                    Marshal.Copy(rowStart, raw, y * rowBytes, rowBytes);
                }
                if (!asRgb) return raw;
                byte[] rgb = new byte[width * height * 3];
                for (int i = 0, j = 0; i < raw.Length; i += 4, j += 3)
                {
                    rgb[j] = raw[i + 2]; rgb[j + 1] = raw[i + 1]; rgb[j + 2] = raw[i];
                }
                return rgb;
            }
            finally { bmp.UnlockBits(data); }
        }
    }
}
"@

Add-Type -TypeDefinition $Native -ReferencedAssemblies System.Drawing, System.Windows.Forms

[JarvisScreen]::DeclareDpiAware()

$config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$token = [string]$config.token
$mailbox = [string]$config.mailbox
$protocolVersion = 1
$landingTolerance = 2

if (-not (Test-Path -LiteralPath $mailbox)) { New-Item -ItemType Directory -Path $mailbox -Force | Out-Null }

# Virtual-key names shared with the host vocabulary (jarvis/cu/actuate/base.py).
$VkMap = @{
    'ctrl' = 0x11; 'control' = 0x11; 'shift' = 0x10; 'alt' = 0x12; 'menu' = 0x12; 'option' = 0x12
    'win' = 0x5B; 'windows' = 0x5B; 'lwin' = 0x5B; 'rwin' = 0x5C; 'cmd' = 0x5B; 'command' = 0x5B
    'meta' = 0x5B; 'super' = 0x5B
    'esc' = 0x1B; 'escape' = 0x1B; 'enter' = 0x0D; 'return' = 0x0D; 'tab' = 0x09
    'space' = 0x20; 'spacebar' = 0x20; 'backspace' = 0x08; 'back' = 0x08
    'delete' = 0x2E; 'del' = 0x2E; 'insert' = 0x2D; 'ins' = 0x2D
    'home' = 0x24; 'end' = 0x23; 'pageup' = 0x21; 'pgup' = 0x21; 'pagedown' = 0x22; 'pgdn' = 0x22
    'left' = 0x25; 'up' = 0x26; 'right' = 0x27; 'down' = 0x28; 'capslock' = 0x14
    'multiply' = 0x6A; 'add' = 0x6B; 'subtract' = 0x6D; 'decimal' = 0x6E; 'divide' = 0x6F
}
1..12 | ForEach-Object { $VkMap["f$_"] = 0x6F + $_ }
0..9 | ForEach-Object { $VkMap["numpad$_"] = 0x60 + $_ }
# Keys the OS only routes correctly with the extended-key flag set.
$ExtendedKeys = @('insert', 'ins', 'delete', 'del', 'home', 'end', 'pageup', 'pgup',
    'pagedown', 'pgdn', 'left', 'up', 'right', 'down', 'divide', 'rwin')

function Resolve-Vk {
    param([string]$Name)
    $key = $Name.ToLowerInvariant()
    if ($VkMap.ContainsKey($key)) { return [int]$VkMap[$key] }
    if ($key.Length -eq 1) {
        $ch = [char]::ToUpperInvariant($key[0])
        if (($ch -ge 'A' -and $ch -le 'Z') -or ($ch -ge '0' -and $ch -le '9')) { return [int][byte][char]$ch }
    }
    throw "unknown key name '$Name'"
}

function Test-Extended { param([string]$Name) return $ExtendedKeys -contains $Name.ToLowerInvariant() }

# Position, read back, and refuse to press when the cursor did not land:
# the host contract is that a silent miss is worse than a loud failure.
function Invoke-VerifiedMove {
    param([int]$X, [int]$Y)
    for ($attempt = 0; $attempt -lt 2; $attempt++) {
        [JarvisScreen]::MoveAbsolute($X, $Y)
        Start-Sleep -Milliseconds 20
        $pos = [JarvisScreen]::CursorPos()
        if ($null -eq $pos) { continue }
        if ([Math]::Abs($pos[0] - $X) -le $landingTolerance -and [Math]::Abs($pos[1] - $Y) -le $landingTolerance) { return }
    }
    throw "cursor did not land on ($X,$Y) — refusing to press a button"
}

function Invoke-ScreenAction {
    param([string]$Action, $Params)
    switch ($Action) {
        'click' {
            Invoke-VerifiedMove -X ([int]$Params.x) -Y ([int]$Params.y)
            $button = 'left'; if ($Params.PSObject.Properties.Name -contains 'button' -and $Params.button) { $button = [string]$Params.button }
            $double = $false; if ($Params.PSObject.Properties.Name -contains 'double') { $double = [bool]$Params.double }
            [JarvisScreen]::ClickAtCursor($button, $double)
            return "click at ($($Params.x),$($Params.y))"
        }
        'drag' {
            Invoke-VerifiedMove -X ([int]$Params.x1) -Y ([int]$Params.y1)
            [JarvisScreen]::ButtonDown('left')
            $steps = 12
            for ($i = 1; $i -le $steps; $i++) {
                $ix = [int]([int]$Params.x1 + (([int]$Params.x2 - [int]$Params.x1) * $i / $steps))
                $iy = [int]([int]$Params.y1 + (([int]$Params.y2 - [int]$Params.y1) * $i / $steps))
                [JarvisScreen]::MoveAbsolute($ix, $iy)
                Start-Sleep -Milliseconds 15
            }
            [JarvisScreen]::ButtonUp('left')
            return 'drag'
        }
        'type_text' {
            [JarvisScreen]::TypeText([string]$Params.text)
            return "typed"
        }
        'hotkey' {
            $names = @($Params.keys | ForEach-Object { [string]$_ })
            if ($names.Count -eq 0) { throw 'hotkey needs at least one key' }
            $vks = @(); $ext = @()
            foreach ($n in $names) { $vks += (Resolve-Vk -Name $n); $ext += (Test-Extended -Name $n) }
            for ($i = 0; $i -lt $vks.Count; $i++) { [JarvisScreen]::KeyDown([uint16]$vks[$i], [bool]$ext[$i]) }
            for ($i = $vks.Count - 1; $i -ge 0; $i--) { [JarvisScreen]::KeyUp([uint16]$vks[$i], [bool]$ext[$i]) }
            return "key $($names -join '+')"
        }
        'scroll' {
            if ($Params.PSObject.Properties.Name -contains 'x' -and $null -ne $Params.x) {
                Invoke-VerifiedMove -X ([int]$Params.x) -Y ([int]$Params.y)
            }
            $direction = [string]$Params.direction
            $amount = 3; if ($Params.PSObject.Properties.Name -contains 'amount') { $amount = [int]$Params.amount }
            switch ($direction) {
                'up' { [JarvisScreen]::Wheel($amount, $false) }
                'down' { [JarvisScreen]::Wheel(-$amount, $false) }
                'right' { [JarvisScreen]::Wheel($amount, $true) }
                'left' { [JarvisScreen]::Wheel(-$amount, $true) }
                default { throw "unknown scroll direction '$direction'" }
            }
            return "scroll $direction x$amount"
        }
        'switch_window' {
            $needle = [string]$Params.title_contains
            if (-not [JarvisScreen]::FocusWindowByTitle($needle)) { throw "no window matching '$needle'" }
            return "focused '$needle'"
        }
        'open_app' {
            $name = [string]$Params.app_name
            if ([string]::IsNullOrWhiteSpace($name)) { throw 'open_app needs an app name' }
            if ([JarvisScreen]::FocusWindowByTitle($name)) { return "focused $name" }
            Start-Process -FilePath $name -ErrorAction Stop | Out-Null
            $deadline = (Get-Date).AddSeconds(8)
            while ((Get-Date) -lt $deadline) {
                if ([JarvisScreen]::FocusWindowByTitle($name)) { return "launched $name" }
                Start-Sleep -Milliseconds 150
            }
            return "launched $name (no window yet)"
        }
        default { throw "unknown screen action '$Action'" }
    }
}

function Write-Atomic {
    param([string]$Path, [byte[]]$Bytes)
    $tmp = "$Path.part"
    [System.IO.File]::WriteAllBytes($tmp, $Bytes)
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Force }
    [System.IO.File]::Move($tmp, $Path)
}

function Write-Response {
    param([string]$Seq, $Envelope, [byte[]]$Blob)
    if ($null -ne $Blob -and $Blob.Length -gt 0) {
        Write-Atomic -Path (Join-Path $mailbox "$Seq.res.bin") -Bytes $Blob
    }
    $json = ($Envelope | ConvertTo-Json -Depth 6 -Compress)
    Write-Atomic -Path (Join-Path $mailbox "$Seq.res.json") -Bytes ([System.Text.Encoding]::UTF8.GetBytes($json))
}

$running = $true
Write-Host "[jarvis-screen] runner up, mailbox $mailbox"

while ($running) {
    $requests = @(Get-ChildItem -LiteralPath $mailbox -Filter '*.req.json' -ErrorAction SilentlyContinue | Sort-Object Name)
    if ($requests.Count -eq 0) { Start-Sleep -Milliseconds 15; continue }

    foreach ($file in $requests) {
        $seq = $file.Name.Substring(0, $file.Name.Length - '.req.json'.Length)
        $envelope = $null
        $blob = $null
        try {
            $request = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        }
        catch {
            # A torn read of a file the host is still replacing — the next poll
            # sees the settled version.
            continue
        }
        try {
            if ([string]$request.token -ne $token) {
                $envelope = @{ ok = $false; error = 'bad token' }
            }
            else {
                $method = [string]$request.method
                $p = $request.params
                switch ($method) {
                    'health' { $envelope = @{ ok = $true; v = $protocolVersion; kind = 'windows-sandbox' } }
                    'geometry' {
                        $v = [JarvisScreen]::VirtualScreen()
                        $envelope = @{ ok = $true; left = $v[0]; top = $v[1]; width = $v[2]; height = $v[3] }
                    }
                    'grab' {
                        $w = 0; $h = 0
                        $asRgb = $false
                        if ($p.PSObject.Properties.Name -contains 'rgb') { $asRgb = [bool]$p.rgb }
                        $blob = [JarvisScreen]::Grab([int]$p.left, [int]$p.top, [int]$p.width, [int]$p.height, $asRgb, [ref]$w, [ref]$h)
                        $envelope = @{ ok = $true; binary = $true; width = $w; height = $h; format = $(if ($asRgb) { 'RGB' } else { 'BGRX' }) }
                    }
                    'foreground' {
                        $fg = [JarvisScreen]::Foreground()
                        if ($null -eq $fg) { $envelope = @{ ok = $true; available = $false } }
                        else {
                            $envelope = @{ ok = $true; available = $true; handle = $fg[0]; title = $fg[1]; app = ''
                                rect = @($fg[2], $fg[3], $fg[4], $fg[5])
                            }
                        }
                    }
                    # A fresh sandbox exposes no accessibility bridge we can
                    # trust, and claiming otherwise would turn the host's
                    # strict verification into a rubber stamp. Answering
                    # "unsupported" makes the host ground on pixels, which is
                    # both honest and correct here.
                    'ui_snapshot' { $envelope = @{ ok = $true; supported = $false } }
                    'typed_text_landed' { $envelope = @{ ok = $true; landed = $null } }
                    'click_landed_in_focus' { $envelope = @{ ok = $true; focused = $null } }
                    'act' {
                        try {
                            $detail = Invoke-ScreenAction -Action ([string]$p.action) -Params $p.params
                            $envelope = @{ ok = $true; detail = $detail }
                        }
                        catch {
                            $envelope = @{ ok = $false; error = $_.Exception.Message }
                        }
                    }
                    'quit' { $envelope = @{ ok = $true }; $running = $false }
                    default { $envelope = @{ ok = $false; error = "unknown method '$method'" } }
                }
            }
        }
        catch {
            $envelope = @{ ok = $false; error = $_.Exception.Message }
        }
        try {
            Write-Response -Seq $seq -Envelope $envelope -Blob $blob
            Remove-Item -LiteralPath $file.FullName -Force -ErrorAction SilentlyContinue
        }
        catch {
            Write-Host "[jarvis-screen] could not answer ${seq}: $($_.Exception.Message)"
        }
    }
}

Write-Host '[jarvis-screen] runner stopped'
