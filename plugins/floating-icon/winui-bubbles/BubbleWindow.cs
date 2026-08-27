using System.Runtime.InteropServices;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;
using Windows.Graphics;
using Windows.System;
using Windows.UI;

namespace OpenWorker.WinUIBubbles;

internal sealed class BubbleWindow : Window
{
    private const int GwlExStyle = -20;
    private const int GwlStyle = -16;
    private const int SmXVirtualScreen = 76;
    private const int SmYVirtualScreen = 77;
    private const int SmCxVirtualScreen = 78;
    private const int SmCyVirtualScreen = 79;
    private const int DwmwaNcRenderingPolicy = 2;
    private const int DwmwaWindowCornerPreference = 33;
    private const int DwmwaBorderColor = 34;
    private const int DwmncrpDisabled = 1;
    private const int DwmwcpDoNotRound = 1;
    private const int DwmColorNone = unchecked((int)0xFFFFFFFE);
    private const long WsExToolWindow = 0x00000080L;
    private const long WsExNoActivate = 0x08000000L;
    private const long WsExTransparent = 0x00000020L;
    private const long WsCaption = 0x00C00000L;
    private const long WsSysMenu = 0x00080000L;
    private const long WsThickFrame = 0x00040000L;
    private const long WsMinimizeBox = 0x00020000L;
    private const long WsMaximizeBox = 0x00010000L;
    private const uint SwpNoSize = 0x0001;
    private const uint SwpNoMove = 0x0002;
    private const uint SwpNoZOrder = 0x0004;
    private const uint SwpFrameChanged = 0x0020;

    private readonly double _widthDip;
    private readonly double _heightDip;
    private readonly double _radiusDip;
    private readonly bool _clickThrough;
    private TextBlock? _hudText;
    private TextBlock? _progressText;
    private TextBlock? _noticeText;
    private IntPtr _hwnd;

    private BubbleWindow(
        UIElement content,
        double widthDip,
        double heightDip,
        double radiusDip,
        bool clickThrough,
        Action? cancelAction = null)
    {
        _widthDip = widthDip;
        _heightDip = heightDip;
        _radiusDip = radiusDip;
        _clickThrough = clickThrough;
        Content = content;
        if (cancelAction is not null)
        {
            var escape = new KeyboardAccelerator { Key = VirtualKey.Escape };
            escape.Invoked += (_, args) =>
            {
                args.Handled = true;
                cancelAction();
            };
            content.KeyboardAccelerators.Add(escape);
        }
        ExtendsContentIntoTitleBar = true;
        Activated += OnActivated;
        Activate();
        ConfigureNativeWindow();
        AppWindow.Hide();
    }

    public static BubbleWindow CreateMenu(Action<object> emit)
    {
        var panel = new StackPanel { Spacing = 2 };
        panel.Children.Add(ActionButton("识图", () => emit(new { @event = "menu", action = "vision" })));
        panel.Children.Add(new Rectangle
        {
            Height = 1,
            Fill = Brush("#E0E0E0"),
            Margin = new Thickness(8, 2, 8, 2),
        });
        panel.Children.Add(ActionButton("关闭悬浮窗", () => emit(new { @event = "menu", action = "close" }), danger: true));
        return new BubbleWindow(
            // Content is 93 DIP high; keep one spare DIP for fractional scaling.
            Surface(panel, new Thickness(4), 12), 190, 94, 12, clickThrough: false,
            cancelAction: () => emit(new { @event = "menu", action = "cancel" }));
    }

    public static BubbleWindow CreateToolbar(Action<object> emit)
    {
        var panel = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 2 };
        panel.Children.Add(ActionButton("提取文字", () => emit(new { @event = "toolbar", action = "extract" }), compact: true));
        panel.Children.Add(ActionButton("翻译", () => emit(new { @event = "toolbar", action = "translate" }), compact: true));
        panel.Children.Add(ActionButton("解答", () => emit(new { @event = "toolbar", action = "answer" }), compact: true));
        panel.Children.Add(ActionButton("取消", () => emit(new { @event = "toolbar", action = "cancel" }), danger: true, compact: true));
        return new BubbleWindow(
            Surface(panel, new Thickness(4), 12), 352, 48, 12, clickThrough: false,
            cancelAction: () => emit(new { @event = "toolbar", action = "cancel" }));
    }

    public static BubbleWindow CreateHud()
    {
        var text = new TextBlock
        {
            Foreground = Brush("#242424"),
            FontFamily = new FontFamily("Microsoft YaHei UI"),
            FontSize = 12,
            VerticalAlignment = VerticalAlignment.Center,
        };
        var window = new BubbleWindow(Surface(text, new Thickness(10, 6, 10, 6), 8), 300, 34, 8, clickThrough: true);
        window._hudText = text;
        return window;
    }

    public static BubbleWindow CreateProgress(Action<object> emit)
    {
        var text = new TextBlock
        {
            Text = "正在处理截图…",
            Foreground = Brush("#242424"),
            FontFamily = new FontFamily("Microsoft YaHei UI"),
            FontSize = 13,
            VerticalAlignment = VerticalAlignment.Center,
        };
        var panel = new Grid { ColumnSpacing = 12 };
        panel.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        panel.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        panel.Children.Add(text);
        var cancel = ActionButton("取消", () => emit(new { @event = "progress", action = "cancel" }), danger: true, compact: true);
        Grid.SetColumn(cancel, 1);
        panel.Children.Add(cancel);
        var window = new BubbleWindow(
            Surface(panel, new Thickness(12, 8, 8, 8), 12), 320, 58, 12, clickThrough: false,
            cancelAction: () => emit(new { @event = "progress", action = "cancel" }));
        window._progressText = text;
        return window;
    }

    public static BubbleWindow CreateNotice(Action dismiss)
    {
        var text = new TextBlock
        {
            Foreground = Brush("#242424"),
            FontFamily = new FontFamily("Microsoft YaHei UI"),
            FontSize = 13,
            TextWrapping = TextWrapping.Wrap,
            VerticalAlignment = VerticalAlignment.Center,
        };
        var panel = new Grid { ColumnSpacing = 12 };
        panel.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        panel.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        panel.Children.Add(text);
        var close = ActionButton("知道了", dismiss, compact: true);
        Grid.SetColumn(close, 1);
        panel.Children.Add(close);
        var window = new BubbleWindow(
            Surface(panel, new Thickness(14, 10, 10, 10), 12), 420, 82, 12, clickThrough: false,
            cancelAction: dismiss);
        window._noticeText = text;
        return window;
    }

    public void SetHudText(string text)
    {
        if (_hudText is not null)
        {
            _hudText.Text = text;
        }
    }

    public void SetProgressText(string text)
    {
        if (_progressText is not null)
        {
            _progressText.Text = text;
        }
    }

    public void SetNoticeText(string text)
    {
        if (_noticeText is not null)
        {
            _noticeText.Text = text;
        }
    }

    public void ShowAt(int x, int y, bool activate)
    {
        // XamlRoot can briefly report 1.0 while this always-hidden-on-startup
        // window is being presented for the first time. The XAML tree still
        // renders at the monitor DPI, so sizing from that transient value clips
        // the right and bottom edges at 125%+. HWND DPI is authoritative here.
        var scale = Math.Max(1.0, GetDpiForWindow(_hwnd) / 96.0);
        var width = Math.Max(1, (int)Math.Ceiling(_widthDip * scale));
        var height = Math.Max(1, (int)Math.Ceiling(_heightDip * scale));
        (x, y) = ClampToVirtualScreen(x, y, width, height);
        AppWindow.MoveAndResize(new RectInt32(x, y, width, height));
        ApplyRoundedRegion(width, height, scale);
        AppWindow.Show(activate && !_clickThrough);
    }

    private static (int X, int Y) ClampToVirtualScreen(int x, int y, int width, int height)
    {
        const int margin = 8;
        var left = GetSystemMetrics(SmXVirtualScreen);
        var top = GetSystemMetrics(SmYVirtualScreen);
        var right = left + GetSystemMetrics(SmCxVirtualScreen);
        var bottom = top + GetSystemMetrics(SmCyVirtualScreen);
        var maxX = Math.Max(left + margin, right - width - margin);
        var maxY = Math.Max(top + margin, bottom - height - margin);
        return (
            Math.Clamp(x, left + margin, maxX),
            Math.Clamp(y, top + margin, maxY)
        );
    }

    public void Hide()
    {
        AppWindow.Hide();
    }

    private void ConfigureNativeWindow()
    {
        _hwnd = WinRT.Interop.WindowNative.GetWindowHandle(this);
        if (AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.SetBorderAndTitleBar(false, false);
            presenter.IsResizable = false;
            presenter.IsMaximizable = false;
            presenter.IsMinimizable = false;
            presenter.IsAlwaysOnTop = true;
        }

        // OverlappedPresenter keeps WS_DLGFRAME/WS_SYSMENU on some Windows
        // builds even after SetBorderAndTitleBar(false, false). Strip every
        // non-client frame style so only the XAML surface draws an outline.
        var windowStyle = GetWindowLongPtr(_hwnd, GwlStyle).ToInt64();
        windowStyle &= ~(WsCaption | WsSysMenu | WsThickFrame | WsMinimizeBox | WsMaximizeBox);
        SetWindowLongPtr(_hwnd, GwlStyle, new IntPtr(windowStyle));
        SetWindowPos(
            _hwnd, IntPtr.Zero, 0, 0, 0, 0,
            SwpNoMove | SwpNoSize | SwpNoZOrder | SwpFrameChanged);

        var style = GetWindowLongPtr(_hwnd, GwlExStyle).ToInt64() | WsExToolWindow;
        if (_clickThrough)
        {
            style |= WsExNoActivate | WsExTransparent;
        }
        SetWindowLongPtr(_hwnd, GwlExStyle, new IntPtr(style));

        // The XAML Border owns the visible outline and exact radius. Disable the
        // entire DWM non-client rendering path (including its outline/shadow),
        // which otherwise produces a second contour around the light surface.
        var ncRenderingPolicy = DwmncrpDisabled;
        _ = DwmSetWindowAttribute(
            _hwnd, DwmwaNcRenderingPolicy,
            ref ncRenderingPolicy, Marshal.SizeOf<int>());
        var cornerPreference = DwmwcpDoNotRound;
        _ = DwmSetWindowAttribute(
            _hwnd, DwmwaWindowCornerPreference,
            ref cornerPreference, Marshal.SizeOf<int>());
        var borderColor = DwmColorNone;
        _ = DwmSetWindowAttribute(
            _hwnd, DwmwaBorderColor,
            ref borderColor, Marshal.SizeOf<int>());
    }

    private void ApplyRoundedRegion(int width, int height, double scale)
    {
        var diameter = Math.Max(2, (int)Math.Round(_radiusDip * 2 * scale));
        var region = CreateRoundRectRgn(0, 0, width + 1, height + 1, diameter, diameter);
        if (region != IntPtr.Zero)
        {
            SetWindowRgn(_hwnd, region, true);
        }
    }

    private void OnActivated(object sender, WindowActivatedEventArgs args)
    {
        if (!_clickThrough && args.WindowActivationState == WindowActivationState.Deactivated)
        {
            AppWindow.Hide();
        }
    }

    private static Border Surface(UIElement child, Thickness padding, double radius)
        => new()
        {
            Child = child,
            Background = Brush("#FFFFFF"),
            BorderBrush = Brush("#D1D1D1"),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(radius),
            Padding = padding,
        };

    private static Button ActionButton(string label, Action action, bool danger = false, bool compact = false)
    {
        var transparent = new SolidColorBrush(Color.FromArgb(0, 0, 0, 0));
        var button = new Button
        {
            Content = label,
            Height = compact ? 38 : 38,
            Padding = compact ? new Thickness(10, 0, 10, 0) : new Thickness(12, 0, 12, 0),
            HorizontalAlignment = compact ? HorizontalAlignment.Left : HorizontalAlignment.Stretch,
            HorizontalContentAlignment = HorizontalAlignment.Left,
            Background = transparent,
            Foreground = Brush(danger ? "#B10E1C" : "#242424"),
            BorderThickness = new Thickness(0),
            CornerRadius = new CornerRadius(4),
            IsTabStop = false,
            AllowFocusOnInteraction = false,
            UseSystemFocusVisuals = false,
            FontFamily = new FontFamily("Microsoft YaHei UI"),
            FontSize = 14,
        };
        // WinUI's stock pointer-over fill is too dark for this compact light bubble.
        // Override only the visual-state resources and retain native Button input,
        // focus, accessibility, and keyboard behavior.
        button.Resources["ButtonBackground"] = transparent;
        button.Resources["ButtonBackgroundPointerOver"] = Brush("#F5F5F5");
        button.Resources["ButtonBackgroundPressed"] = Brush("#EBEBEB");
        button.Resources["ButtonBorderBrush"] = transparent;
        button.Resources["ButtonBorderBrushPointerOver"] = transparent;
        button.Resources["ButtonBorderBrushPressed"] = transparent;
        button.Click += (_, _) => action();
        return button;
    }

    private static SolidColorBrush Brush(string hex)
    {
        var value = hex.TrimStart('#');
        return new SolidColorBrush(Color.FromArgb(
            255,
            Convert.ToByte(value[0..2], 16),
            Convert.ToByte(value[2..4], 16),
            Convert.ToByte(value[4..6], 16)));
    }

    [DllImport("gdi32.dll")]
    private static extern IntPtr CreateRoundRectRgn(int left, int top, int right, int bottom, int widthEllipse, int heightEllipse);

    [DllImport("user32.dll")]
    private static extern int SetWindowRgn(IntPtr hwnd, IntPtr region, bool redraw);

    [DllImport("user32.dll")]
    private static extern bool SetWindowPos(
        IntPtr hwnd, IntPtr insertAfter, int x, int y, int width, int height, uint flags);

    [DllImport("user32.dll")]
    private static extern int GetSystemMetrics(int index);

    [DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(IntPtr hwnd);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")]
    private static extern IntPtr GetWindowLongPtr64(IntPtr hwnd, int index);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongW")]
    private static extern IntPtr GetWindowLong32(IntPtr hwnd, int index);

    private static IntPtr GetWindowLongPtr(IntPtr hwnd, int index)
        => IntPtr.Size == 8 ? GetWindowLongPtr64(hwnd, index) : GetWindowLong32(hwnd, index);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")]
    private static extern IntPtr SetWindowLongPtr64(IntPtr hwnd, int index, IntPtr value);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongW")]
    private static extern IntPtr SetWindowLong32(IntPtr hwnd, int index, IntPtr value);

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(
        IntPtr hwnd, int attribute, ref int value, int valueSize);

    private static IntPtr SetWindowLongPtr(IntPtr hwnd, int index, IntPtr value)
        => IntPtr.Size == 8 ? SetWindowLongPtr64(hwnd, index, value) : SetWindowLong32(hwnd, index, value);
}
