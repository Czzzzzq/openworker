using System.Text.Json;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;

namespace OpenWorker.WinUIBubbles;

internal sealed class BubbleHost
{
    private readonly DispatcherQueue _dispatcher;
    private readonly object _outputLock = new();
    private BubbleWindow? _menu;
    private BubbleWindow? _hud;
    private BubbleWindow? _toolbar;
    private BubbleWindow? _progress;
    private BubbleWindow? _notice;
    private IconWindow? _icon;
    private SelectionWindow? _selection;
    private Windows.Graphics.RectInt32? _selectedRegion;

    public BubbleHost(DispatcherQueue dispatcher)
    {
        _dispatcher = dispatcher;
    }

    public void Start()
    {
        _menu = BubbleWindow.CreateMenu(HandleMenu);
        _hud = BubbleWindow.CreateHud();
        _toolbar = BubbleWindow.CreateToolbar(HandleToolbar);
        _icon = new IconWindow(Emit, ShowMenu, ShowHud, HideHud);
        _selection = new SelectionWindow(ShowHud, HideHud, SelectionReady, CancelSelection);
        _notice = BubbleWindow.CreateNotice(() => _notice?.Hide());
        // Progress is created lazily; an active ProgressRing in a hidden WinUI window can
        // initialize the composition animation before that HWND has ever been presented.
        Emit(new { @event = "ready" });
        _ = Task.Run(ReadCommandsAsync);
    }

    private async Task ReadCommandsAsync()
    {
        string? line;
        while ((line = await Console.In.ReadLineAsync()) is not null)
        {
            if (string.IsNullOrWhiteSpace(line))
            {
                continue;
            }

            JsonElement command;
            try
            {
                using var document = JsonDocument.Parse(line);
                command = document.RootElement.Clone();
            }
            catch (JsonException)
            {
                Emit(new { @event = "error", message = "invalid JSON command" });
                continue;
            }

            _dispatcher.TryEnqueue(() => Handle(command));
        }

        _dispatcher.TryEnqueue(Shutdown);
    }

    private void Handle(JsonElement message)
    {
        var command = StringValue(message, "cmd");
        switch (command)
        {
            case "initialize":
                _icon!.Initialize(
                    StringValue(message, "icon_path"),
                    StringValue(message, "version"),
                    IntValue(message, "x"),
                    IntValue(message, "y"));
                break;
            case "show_icon":
                var iconPosition = _icon!.Position;
                _icon.ShowAt(iconPosition.X, iconPosition.Y);
                break;
            case "hide_icon":
                _icon!.HideIcon();
                break;
            case "start_selection":
                StartSelection();
                break;
            case "show_menu":
                _menu!.ShowAt(IntValue(message, "x"), IntValue(message, "y"), activate: true);
                break;
            case "hide_menu":
                _menu!.Hide();
                break;
            case "show_hud":
                _hud!.SetHudText(StringValue(message, "text"));
                _hud.ShowAt(IntValue(message, "x"), IntValue(message, "y"), activate: false);
                break;
            case "hide_hud":
                _hud!.Hide();
                break;
            case "show_toolbar":
                _toolbar!.ShowAt(IntValue(message, "x"), IntValue(message, "y"), activate: true);
                break;
            case "hide_toolbar":
                _toolbar!.Hide();
                break;
            case "show_progress":
                _progress ??= BubbleWindow.CreateProgress(Emit);
                _progress!.SetProgressText(StringValue(message, "text"));
                _progress.ShowAt(IntValue(message, "x"), IntValue(message, "y"), activate: true);
                break;
            case "update_progress":
                _progress!.SetProgressText(StringValue(message, "text"));
                break;
            case "hide_progress":
                _progress!.Hide();
                break;
            case "show_notice":
                ShowNotice(StringValue(message, "text"));
                break;
            case "hide_notice":
                _notice!.Hide();
                break;
            case "hide_all":
                _menu!.Hide();
                _hud!.Hide();
                _toolbar!.Hide();
                _progress?.Hide();
                _notice!.Hide();
                break;
            case "shutdown":
                Shutdown();
                break;
            case "ping":
                Emit(new { @event = "pong", text = StringValue(message, "text") });
                break;
            default:
                Emit(new { @event = "error", message = $"unknown command: {command}" });
                break;
        }
    }

    private void ShowMenu(int x, int y)
    {
        _hud!.Hide();
        _menu!.ShowAt(x, y, activate: true);
    }

    private void ShowHud(int x, int y, string text)
    {
        _hud!.SetHudText(text);
        _hud.ShowAt(x, y, activate: false);
    }

    private void HideHud() => _hud!.Hide();

    private void HandleMenu(object value)
    {
        var action = ActionValue(value);
        _menu!.Hide();
        if (action == "vision")
        {
            StartSelection();
        }
        else if (action == "close")
        {
            var position = _icon!.Position;
            Emit(new { @event = "icon", action = "close", x = position.X, y = position.Y });
        }
    }

    private void StartSelection()
    {
        _menu!.Hide();
        _icon!.HideIcon();
        NativeWindow.FlushComposition();
        _selectedRegion = null;
        try
        {
            _selection!.Begin();
        }
        catch (Exception exception)
        {
            var position = _icon.Position;
            _icon.ShowAt(position.X, position.Y);
            ShowNotice($"识图启动失败：\n{exception.Message}");
        }
    }

    private void SelectionReady(Windows.Graphics.RectInt32 region)
    {
        _selectedRegion = region;
        _hud!.Hide();
        var screen = NativeWindow.VirtualScreen();
        const int width = 352;
        const int height = 48;
        const int gap = 10;
        var x = Math.Clamp(region.X, screen.X + 8, screen.X + screen.Width - width - 8);
        var below = region.Y + region.Height + gap;
        var above = region.Y - height - gap;
        var y = below + height <= screen.Y + screen.Height
            ? below
            : Math.Max(screen.Y + 8, above);
        _toolbar!.ShowAt(x, y, activate: true);
    }

    private void HandleToolbar(object value)
    {
        var action = ActionValue(value);
        if (_selectedRegion is null || action is not ("extract" or "translate" or "answer"))
        {
            CancelSelection();
            return;
        }

        var region = _selectedRegion.Value;
        _toolbar!.Hide();
        _selection!.End();
        NativeWindow.FlushComposition();
        _selectedRegion = null;
        Emit(new
        {
            @event = "selection",
            action,
            left = region.X,
            top = region.Y,
            right = region.X + region.Width,
            bottom = region.Y + region.Height,
        });
    }

    private void CancelSelection()
    {
        _toolbar!.Hide();
        _selection!.End();
        _selectedRegion = null;
        var position = _icon!.Position;
        _icon.ShowAt(position.X, position.Y);
        Emit(new { @event = "selection", action = "cancel" });
    }

    private void ShowNotice(string text)
    {
        var screen = NativeWindow.VirtualScreen();
        const int width = 420;
        const int height = 82;
        _notice!.SetNoticeText(text);
        _notice.ShowAt(
            screen.X + (screen.Width - width) / 2,
            screen.Y + (screen.Height - height) / 2,
            activate: true);
    }

    private static string ActionValue(object value)
    {
        using var document = JsonDocument.Parse(JsonSerializer.Serialize(value));
        return StringValue(document.RootElement, "action");
    }

    private void Emit(object value)
    {
        lock (_outputLock)
        {
            Console.Out.WriteLine(JsonSerializer.Serialize(value));
            Console.Out.Flush();
        }
    }

    private void Shutdown()
    {
        _menu?.Close();
        _hud?.Close();
        _toolbar?.Close();
        _progress?.Close();
        _notice?.Close();
        _selection?.Close();
        _icon?.Close();
        Environment.Exit(0);
    }

    private static string StringValue(JsonElement element, string name)
        => element.TryGetProperty(name, out var value) ? value.GetString() ?? string.Empty : string.Empty;

    private static int IntValue(JsonElement element, string name)
        => element.TryGetProperty(name, out var value) && value.TryGetInt32(out var number) ? number : 0;
}
