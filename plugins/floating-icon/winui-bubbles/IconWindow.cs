using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Windows.Graphics;

namespace OpenWorker.WinUIBubbles;

// The WinUI host owns dispatch and every bubble. The icon uses a layered HWND
// so Windows composites the PNG's original alpha instead of an opaque XAML
// top-level-window background.
internal sealed class IconWindow : Window
{
    private const int IconSize = 44;
    private const uint WsPopup = 0x80000000;
    private const uint WsExToolWindow = 0x00000080;
    private const uint WsExLayered = 0x00080000;
    private const uint WsExNoActivate = 0x08000000;
    private const int SwHide = 0;
    private const int SwShowNoActivate = 4;
    private const uint UlwAlpha = 0x00000002;
    private const byte AcSrcOver = 0;
    private const byte AcSrcAlpha = 1;
    private const uint WmMouseMove = 0x0200;
    private const uint WmLButtonDown = 0x0201;
    private const uint WmLButtonUp = 0x0202;
    private const uint WmRButtonDown = 0x0204;
    private const uint WmMouseLeave = 0x02A3;
    private const uint TmeLeave = 0x00000002;
    private const uint SwpNoActivate = 0x0010;
    private const uint SwpShowWindow = 0x0040;
    private static readonly string WindowClassName = $"OpenWorker.LayeredIcon.{Environment.ProcessId}";
    private static readonly WindowProc WindowProcedure = DispatchWindowMessage;
    private static readonly Dictionary<IntPtr, IconWindow> Instances = new();
    private static bool _classRegistered;

    private readonly Action<object> _emit;
    private readonly Action<int, int> _showMenu;
    private readonly Action<int, int, string> _showTip;
    private readonly Action _hideTip;
    private readonly DispatcherQueueTimer _tipTimer;
    private IntPtr _hwnd;
    private string _iconPath = string.Empty;
    private string _version = string.Empty;
    private bool _leftPressed;
    private bool _dragged;
    private bool _trackingMouse;
    private PointInt32 _pressCursor;
    private PointInt32 _pressWindow;
    private int _x;
    private int _y;

    public IconWindow(
        Action<object> emit,
        Action<int, int> showMenu,
        Action<int, int, string> showTip,
        Action hideTip)
    {
        _emit = emit;
        _showMenu = showMenu;
        _showTip = showTip;
        _hideTip = hideTip;
        Activate();
        _ = NativeWindow.Configure(this);
        AppWindow.Hide();
        RegisterWindowClass();
        _hwnd = CreateWindowEx(
            WsExLayered | WsExToolWindow | WsExNoActivate,
            WindowClassName, "OpenWorker", WsPopup,
            0, 0, IconSize, IconSize,
            IntPtr.Zero, IntPtr.Zero, GetModuleHandle(null), IntPtr.Zero);
        if (_hwnd == IntPtr.Zero)
        {
            throw new InvalidOperationException("Unable to create the layered icon window.");
        }
        Instances[_hwnd] = this;

        _tipTimer = DispatcherQueue.GetForCurrentThread().CreateTimer();
        _tipTimer.Interval = TimeSpan.FromMilliseconds(500);
        _tipTimer.IsRepeating = false;
        _tipTimer.Tick += (_, _) =>
            _showTip(_x + IconSize / 2, _y - 38, $"OpenWorker {_version}");
    }

    public void Initialize(string iconPath, string version, int x, int y)
    {
        _iconPath = iconPath;
        _version = version;
        ShowAt(x, y);
    }

    public void ShowAt(int x, int y)
    {
        _x = x;
        _y = y;
        RenderOriginalPng();
        SetWindowPos(
            _hwnd, new IntPtr(-1), _x, _y, IconSize, IconSize,
            SwpNoActivate | SwpShowWindow);
    }

    public void HideIcon()
    {
        _tipTimer.Stop();
        _hideTip();
        ShowWindow(_hwnd, SwHide);
    }

    public PointInt32 Position => new(_x, _y);

    public new void Close()
    {
        _tipTimer.Stop();
        if (_hwnd == IntPtr.Zero)
        {
            return;
        }
        Instances.Remove(_hwnd);
        DestroyWindow(_hwnd);
        _hwnd = IntPtr.Zero;
        base.Close();
    }

    private void RenderOriginalPng()
    {
        using var bitmap = new System.Drawing.Bitmap(_iconPath);
        if (bitmap.Width != IconSize || bitmap.Height != IconSize)
        {
            throw new InvalidOperationException(
                $"The floating icon must be exactly {IconSize}x{IconSize} pixels.");
        }
        using var premultiplied = new System.Drawing.Bitmap(
            IconSize, IconSize, PixelFormat.Format32bppPArgb);
        using (var graphics = System.Drawing.Graphics.FromImage(premultiplied))
        {
            graphics.Clear(System.Drawing.Color.Transparent);
            graphics.DrawImageUnscaled(bitmap, 0, 0);
        }

        var screenDc = GetDC(IntPtr.Zero);
        var memoryDc = CreateCompatibleDC(screenDc);
        var hBitmap = premultiplied.GetHbitmap(System.Drawing.Color.FromArgb(0));
        var previous = SelectObject(memoryDc, hBitmap);
        try
        {
            var destination = new NativePoint(_x, _y);
            var source = new NativePoint(0, 0);
            var size = new NativeSize(IconSize, IconSize);
            var blend = new BlendFunction
            {
                BlendOp = AcSrcOver,
                SourceConstantAlpha = 255,
                AlphaFormat = AcSrcAlpha,
            };
            if (!UpdateLayeredWindow(
                    _hwnd, screenDc, ref destination, ref size,
                    memoryDc, ref source, 0, ref blend, UlwAlpha))
            {
                throw new InvalidOperationException("Unable to render the layered icon window.");
            }
        }
        finally
        {
            SelectObject(memoryDc, previous);
            DeleteObject(hBitmap);
            DeleteDC(memoryDc);
            ReleaseDC(IntPtr.Zero, screenDc);
        }
    }

    private IntPtr HandleMessage(uint message, IntPtr wParam, IntPtr lParam)
    {
        switch (message)
        {
            case WmLButtonDown:
                _tipTimer.Stop();
                _hideTip();
                _leftPressed = true;
                _dragged = false;
                _pressCursor = NativeWindow.CursorPosition();
                _pressWindow = new PointInt32(_x, _y);
                SetCapture(_hwnd);
                return IntPtr.Zero;
            case WmMouseMove:
                if (_leftPressed)
                {
                    var cursor = NativeWindow.CursorPosition();
                    var dx = cursor.X - _pressCursor.X;
                    var dy = cursor.Y - _pressCursor.Y;
                    if (!_dragged && (Math.Abs(dx) >= 4 || Math.Abs(dy) >= 4))
                    {
                        _dragged = true;
                    }
                    if (_dragged)
                    {
                        _x = _pressWindow.X + dx;
                        _y = _pressWindow.Y + dy;
                        SetWindowPos(
                            _hwnd, new IntPtr(-1), _x, _y, IconSize, IconSize,
                            SwpNoActivate | SwpShowWindow);
                    }
                }
                else if (!_trackingMouse)
                {
                    _trackingMouse = true;
                    var tracking = new TrackMouseEventInfo
                    {
                        Size = (uint)Marshal.SizeOf<TrackMouseEventInfo>(),
                        Flags = TmeLeave,
                        HwndTrack = _hwnd,
                    };
                    TrackMouseEvent(ref tracking);
                    _tipTimer.Start();
                }
                return IntPtr.Zero;
            case WmLButtonUp:
                if (_leftPressed)
                {
                    _leftPressed = false;
                    ReleaseCapture();
                    if (_dragged)
                    {
                        _emit(new { @event = "icon", action = "moved", x = _x, y = _y });
                    }
                    else
                    {
                        _emit(new { @event = "icon", action = "open" });
                    }
                }
                return IntPtr.Zero;
            case WmRButtonDown:
                _tipTimer.Stop();
                _hideTip();
                var cursorPosition = NativeWindow.CursorPosition();
                _showMenu(cursorPosition.X + 10, cursorPosition.Y + 8);
                return IntPtr.Zero;
            case WmMouseLeave:
                _trackingMouse = false;
                _tipTimer.Stop();
                _hideTip();
                return IntPtr.Zero;
            default:
                return DefWindowProc(_hwnd, message, wParam, lParam);
        }
    }

    private static IntPtr DispatchWindowMessage(
        IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam)
        => Instances.TryGetValue(hwnd, out var window)
            ? window.HandleMessage(message, wParam, lParam)
            : DefWindowProc(hwnd, message, wParam, lParam);

    private static void RegisterWindowClass()
    {
        if (_classRegistered)
        {
            return;
        }
        var windowClass = new WindowClassEx
        {
            Size = (uint)Marshal.SizeOf<WindowClassEx>(),
            WindowProcedure = WindowProcedure,
            Instance = GetModuleHandle(null),
            ClassName = WindowClassName,
        };
        if (RegisterClassEx(ref windowClass) == 0)
        {
            throw new InvalidOperationException("Unable to register the layered icon window class.");
        }
        _classRegistered = true;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct NativePoint
    {
        public NativePoint(int x, int y) { X = x; Y = y; }
        public int X;
        public int Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct NativeSize
    {
        public NativeSize(int width, int height) { Width = width; Height = height; }
        public int Width;
        public int Height;
    }

    [StructLayout(LayoutKind.Sequential, Pack = 1)]
    private struct BlendFunction
    {
        public byte BlendOp;
        public byte BlendFlags;
        public byte SourceConstantAlpha;
        public byte AlphaFormat;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct TrackMouseEventInfo
    {
        public uint Size;
        public uint Flags;
        public IntPtr HwndTrack;
        public uint HoverTime;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct WindowClassEx
    {
        public uint Size;
        public uint Style;
        [MarshalAs(UnmanagedType.FunctionPtr)] public WindowProc WindowProcedure;
        public int ClassExtra;
        public int WindowExtra;
        public IntPtr Instance;
        public IntPtr Icon;
        public IntPtr Cursor;
        public IntPtr Background;
        public string? MenuName;
        public string ClassName;
        public IntPtr SmallIcon;
    }

    private delegate IntPtr WindowProc(IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern ushort RegisterClassEx(ref WindowClassEx windowClass);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr CreateWindowEx(
        uint exStyle, string className, string windowName, uint style,
        int x, int y, int width, int height, IntPtr parent, IntPtr menu,
        IntPtr instance, IntPtr parameter);

    [DllImport("user32.dll")]
    private static extern bool DestroyWindow(IntPtr hwnd);

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr hwnd, int command);

    [DllImport("user32.dll")]
    private static extern IntPtr DefWindowProc(IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern IntPtr SetCapture(IntPtr hwnd);

    [DllImport("user32.dll")]
    private static extern bool ReleaseCapture();

    [DllImport("user32.dll")]
    private static extern bool TrackMouseEvent(ref TrackMouseEventInfo tracking);

    [DllImport("user32.dll")]
    private static extern bool SetWindowPos(
        IntPtr hwnd, IntPtr insertAfter, int x, int y, int width, int height, uint flags);

    [DllImport("user32.dll")]
    private static extern IntPtr GetDC(IntPtr hwnd);

    [DllImport("user32.dll")]
    private static extern int ReleaseDC(IntPtr hwnd, IntPtr dc);

    [DllImport("user32.dll")]
    private static extern bool UpdateLayeredWindow(
        IntPtr hwnd, IntPtr destinationDc, ref NativePoint destination,
        ref NativeSize size, IntPtr sourceDc, ref NativePoint source,
        uint colorKey, ref BlendFunction blend, uint flags);

    [DllImport("gdi32.dll")]
    private static extern IntPtr CreateCompatibleDC(IntPtr dc);

    [DllImport("gdi32.dll")]
    private static extern bool DeleteDC(IntPtr dc);

    [DllImport("gdi32.dll")]
    private static extern IntPtr SelectObject(IntPtr dc, IntPtr value);

    [DllImport("gdi32.dll")]
    private static extern bool DeleteObject(IntPtr value);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr GetModuleHandle(string? moduleName);
}
