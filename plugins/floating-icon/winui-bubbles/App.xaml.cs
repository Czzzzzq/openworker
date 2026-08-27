using System.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Dispatching;

namespace OpenWorker.WinUIBubbles;

public partial class App : Application
{
    private BubbleHost? _host;

    public App()
    {
        // The helper is launched with redirected pipes. Pin both sides to UTF-8 so
        // Chinese HUD text is independent of the machine's active OEM code page.
        Console.InputEncoding = new UTF8Encoding(encoderShouldEmitUTF8Identifier: false);
        Console.OutputEncoding = new UTF8Encoding(encoderShouldEmitUTF8Identifier: false);
        InitializeComponent();
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        _host = new BubbleHost(DispatcherQueue.GetForCurrentThread());
        _host.Start();
    }
}
