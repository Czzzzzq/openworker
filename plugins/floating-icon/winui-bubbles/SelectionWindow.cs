using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;
using Windows.Graphics;
using Windows.System;
using Windows.UI;

namespace OpenWorker.WinUIBubbles;

internal sealed class SelectionWindow : Window
{
    private readonly Action<int, int, string> _showHud;
    private readonly Action _hideHud;
    private readonly Action<RectInt32> _selectionReady;
    private readonly Action _cancel;
    private readonly Image _desktop;
    private readonly Rectangle[] _dim = new Rectangle[4];
    private readonly Border _outline;
    private readonly Grid _root;
    private IntPtr _hwnd;
    private RectInt32 _virtual;
    private double _scale = 1;
    private double _logicalWidth;
    private double _logicalHeight;
    private bool _pressed;
    private bool _locked;
    private Windows.Foundation.Point _start;
    private Windows.Foundation.Rect _selection;

    public SelectionWindow(
        Action<int, int, string> showHud,
        Action hideHud,
        Action<RectInt32> selectionReady,
        Action cancel)
    {
        _showHud = showHud;
        _hideHud = hideHud;
        _selectionReady = selectionReady;
        _cancel = cancel;

        _desktop = new Image { Stretch = Stretch.Fill };
        var canvas = new Canvas();
        // Figma Overlay/Mask token: black at 35% opacity (89 / 255).
        var dimBrush = new SolidColorBrush(Color.FromArgb(89, 0, 0, 0));
        for (var i = 0; i < _dim.Length; i++)
        {
            _dim[i] = new Rectangle { Fill = dimBrush };
            canvas.Children.Add(_dim[i]);
        }
        _outline = new Border
        {
            BorderBrush = new SolidColorBrush(Color.FromArgb(255, 0, 229, 255)),
            BorderThickness = new Thickness(2),
            Visibility = Visibility.Collapsed,
        };
        canvas.Children.Add(_outline);

        _root = new Grid { Background = new SolidColorBrush(Color.FromArgb(255, 0, 0, 0)) };
        _root.Children.Add(_desktop);
        _root.Children.Add(canvas);
        _root.PointerPressed += OnPointerPressed;
        _root.PointerMoved += OnPointerMoved;
        _root.PointerReleased += OnPointerReleased;
        var escape = new KeyboardAccelerator { Key = VirtualKey.Escape };
        escape.Invoked += (_, args) =>
        {
            args.Handled = true;
            _cancel();
        };
        _root.KeyboardAccelerators.Add(escape);
        Content = _root;

        ExtendsContentIntoTitleBar = true;
        Activate();
        _hwnd = NativeWindow.Configure(this);
        AppWindow.Hide();
    }

    public void Begin()
    {
        _virtual = NativeWindow.VirtualScreen();
        _desktop.Source = ScreenCapture.Capture(
            _virtual.X, _virtual.Y, _virtual.Width, _virtual.Height);
        _scale = NativeWindow.Scale(_hwnd);
        _logicalWidth = _virtual.Width / _scale;
        _logicalHeight = _virtual.Height / _scale;
        _pressed = false;
        _locked = false;
        _selection = default;
        _outline.Visibility = Visibility.Collapsed;
        LayoutDim(default);
        AppWindow.MoveAndResize(_virtual);
        AppWindow.Show(true);
    }

    public void End()
    {
        _pressed = false;
        _locked = false;
        _hideHud();
        AppWindow.Hide();
    }

    private void OnPointerPressed(object sender, PointerRoutedEventArgs args)
    {
        var point = args.GetCurrentPoint(_root);
        if (!point.Properties.IsLeftButtonPressed)
        {
            return;
        }
        if (_locked)
        {
            if (!_selection.Contains(point.Position))
            {
                _cancel();
            }
            return;
        }
        _pressed = true;
        _start = point.Position;
        _selection = new Windows.Foundation.Rect(_start.X, _start.Y, 0, 0);
        _root.CapturePointer(args.Pointer);
        UpdateVisuals(point.Position);
        args.Handled = true;
    }

    private void OnPointerMoved(object sender, PointerRoutedEventArgs args)
    {
        if (_locked)
        {
            return;
        }
        var point = args.GetCurrentPoint(_root).Position;
        if (_pressed)
        {
            var left = Math.Clamp(Math.Min(_start.X, point.X), 0, _logicalWidth);
            var top = Math.Clamp(Math.Min(_start.Y, point.Y), 0, _logicalHeight);
            var right = Math.Clamp(Math.Max(_start.X, point.X), 0, _logicalWidth);
            var bottom = Math.Clamp(Math.Max(_start.Y, point.Y), 0, _logicalHeight);
            _selection = new Windows.Foundation.Rect(left, top, right - left, bottom - top);
        }
        UpdateVisuals(point);
    }

    private void OnPointerReleased(object sender, PointerRoutedEventArgs args)
    {
        if (!_pressed || _locked)
        {
            return;
        }
        _pressed = false;
        _root.ReleasePointerCapture(args.Pointer);
        _hideHud();
        if (_selection.Width < 4 || _selection.Height < 4)
        {
            _cancel();
            return;
        }
        _locked = true;
        _selectionReady(ToPhysical(_selection));
        args.Handled = true;
    }

    private void UpdateVisuals(Windows.Foundation.Point cursor)
    {
        if (_pressed)
        {
            LayoutDim(_selection);
            _outline.Visibility = Visibility.Visible;
            Canvas.SetLeft(_outline, _selection.X);
            Canvas.SetTop(_outline, _selection.Y);
            _outline.Width = _selection.Width;
            _outline.Height = _selection.Height;
        }
        var screenX = _virtual.X + (int)Math.Round(cursor.X * _scale);
        var screenY = _virtual.Y + (int)Math.Round(cursor.Y * _scale);
        var text = $"坐标 {screenX}, {screenY}";
        if (_pressed)
        {
            text += $"    选区 {(int)Math.Round(_selection.Width * _scale)} × {(int)Math.Round(_selection.Height * _scale)}";
        }
        _showHud(screenX + 16, screenY + 18, text);
    }

    private void LayoutDim(Windows.Foundation.Rect hole)
    {
        var hasHole = hole.Width > 0 && hole.Height > 0;
        var left = hasHole ? hole.X : 0;
        var top = hasHole ? hole.Y : 0;
        var right = hasHole ? hole.Right : 0;
        var bottom = hasHole ? hole.Bottom : 0;

        Place(_dim[0], 0, 0, _logicalWidth, hasHole ? top : _logicalHeight);
        Place(_dim[1], 0, top, left, hasHole ? hole.Height : 0);
        Place(_dim[2], right, top, hasHole ? _logicalWidth - right : 0, hasHole ? hole.Height : 0);
        Place(_dim[3], 0, bottom, _logicalWidth, hasHole ? _logicalHeight - bottom : 0);
    }

    private static void Place(FrameworkElement element, double x, double y, double width, double height)
    {
        Canvas.SetLeft(element, x);
        Canvas.SetTop(element, y);
        element.Width = Math.Max(0, width);
        element.Height = Math.Max(0, height);
    }

    private RectInt32 ToPhysical(Windows.Foundation.Rect rect)
    {
        var left = _virtual.X + (int)Math.Round(rect.X * _scale);
        var top = _virtual.Y + (int)Math.Round(rect.Y * _scale);
        var right = _virtual.X + (int)Math.Round(rect.Right * _scale);
        var bottom = _virtual.Y + (int)Math.Round(rect.Bottom * _scale);
        return new RectInt32(left, top, right - left, bottom - top);
    }
}
