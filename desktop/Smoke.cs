using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using Jiaoqian;

internal static class DesktopSmoke {
    static void Require(bool ok,string description){if(!ok)throw new Exception(description);}
    static void SaveView(Window window,string name){
        window.UpdateLayout();
        var surface=(FrameworkElement)VisualTreeHelper.GetChild(window,0);
        var image=new RenderTargetBitmap((int)Math.Ceiling(surface.ActualWidth),(int)Math.Ceiling(surface.ActualHeight),96,96,PixelFormats.Pbgra32);
        image.Render(surface);var encoder=new PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(image));
        var folder=Path.Combine(Worker.Root,"实测","图形界面");Directory.CreateDirectory(folder);
        using(var file=File.Create(Path.Combine(folder,name+".png"))){encoder.Save(file);}
    }
    [STAThread] public static int Main(){
        var app=new Application();var workbench=new Workbench();var window=workbench.Window;
        window.WindowStartupLocation=WindowStartupLocation.Manual;window.Left=-15000;window.Top=-15000;window.ShowActivated=false;window.ShowInTaskbar=false;
        int code=1;window.Dispatcher.BeginInvoke(DispatcherPriority.ApplicationIdle,new Action(async delegate {
            try {
                Func<string,Button> button=name=>(Button)window.FindName(name);Func<string,string> label=name=>((TextBlock)window.FindName(name)).Text;
                Require(!button("Audit").IsEnabled && !button("Plan").IsEnabled,"empty state");
                var settingsCheck=window.Dispatcher.BeginInvoke(DispatcherPriority.ApplicationIdle,new Action(delegate{
                    var settings=window.OwnedWindows.Cast<Window>().Single();try{
                        var form=(StackPanel)((DockPanel)settings.Content).Children.OfType<ScrollViewer>().Single().Content;
                        var provider=form.Children.OfType<ComboBox>().Single();var fields=form.Children.OfType<StackPanel>().Single();var key=form.Children.OfType<PasswordBox>().Single();
                        Require(provider.SelectedIndex==0 && fields.Visibility==Visibility.Collapsed,"DeepSeek simple defaults");key.Password="dummy-key";provider.SelectedIndex=1;
                        Require(fields.Visibility==Visibility.Visible && fields.Children.OfType<TextBox>().Count()==2 && key.Password=="","custom fields and key isolation");
                    }finally{settings.DialogResult=false;}
                }));button("ConnectAi").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));await settingsCheck.Task;
                Require(((TextBlock)window.FindName("ApiState")).Visibility==Visibility.Collapsed,"cancel keeps local routing");
                var local=(CheckBox)window.FindName("LocalPlan");Require(local.IsChecked!=true,"offline by default");bool live=Environment.GetEnvironmentVariable("JIAOQIAN_GUI_LIVE")=="1";local.IsChecked=live;
                await workbench.Demo();Require(label("Title")=="同一文件夹，两种结果","demo title");
                var demoCards=(StackPanel)window.FindName("Cards");Require(demoCards.Children.Count==2,"two event results");
                var titles=demoCards.Children.Cast<Border>().Select(border=>((TextBlock)((StackPanel)border.Child).Children[1]).Text).ToArray();
                Require(titles[0]=="未发现硬性错误" && titles[1]=="2 项需要修改","actual demo counts");SaveView(window,"两赛对照");
                await workbench.Read(new Dictionary<string,object>{{"path",Path.Combine(Worker.Root,"示例通知","校园短片展-合成通知.txt")}});
                Require(button("Plan").IsEnabled,"notice ready");await workbench.Generate();
                var checkPlan=window.Dispatcher.BeginInvoke(DispatcherPriority.ApplicationIdle,new Action(delegate {
                    var dialog=window.OwnedWindows.Cast<Window>().Single();Require(dialog.Title.Contains("核对规则"),"separate rule window");dialog.DialogResult=false;
                }));button("Inspect").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));await checkPlan.Task;Require(demoCards.Children.Count==0,"no manual cards on plan screen");
                var confirm=(CheckBox)window.FindName("Confirm");Require(confirm.IsEnabled && confirm.IsChecked!=true,"unconfirmed rules");
                workbench.SetFolder(Path.Combine(Worker.Root,"样包","02-修正版"));Require(!button("Audit").IsEnabled,"no scan before confirmation");
                confirm.IsChecked=true;confirm.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));Require(button("Audit").IsEnabled,"ready to scan");
                await workbench.Audit();Require(label("Title")=="2 项需要修改" && button("Report").IsVisible,"audit and report");SaveView(window,"短片展预检");
                Require(demoCards.Children.Count==2 && button("Review").IsVisible,"only errors on main screen");
                var inspect=window.Dispatcher.BeginInvoke(DispatcherPriority.ApplicationIdle,new Action(delegate {
                    var dialog=window.OwnedWindows.Cast<Window>().Single();var layout=(DockPanel)dialog.Content;var scroll=layout.Children.OfType<ScrollViewer>().Single();
                    Require(((StackPanel)scroll.Content).Children.OfType<CheckBox>().Count()>1 && dialog.Title.Contains("人工复核"),"manual items on secondary screen");dialog.DialogResult=false;
                }));button("Review").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));await inspect.Task;Require(label("Title")=="2 项需要修改","back from manual screen preserves errors");
                if(live){
                    var send=window.Dispatcher.BeginInvoke(DispatcherPriority.ApplicationIdle,new Action(delegate {
                        var dialog=window.OwnedWindows.Cast<Window>().Single();var panel=(DockPanel)dialog.Content;var scroll=panel.Children.OfType<ScrollViewer>().Single();
                        var box=((StackPanel)scroll.Content).Children.OfType<CheckBox>().First(item=>Convert.ToInt32(item.Tag)==6);box.IsChecked=true;box.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                        panel.Children.OfType<StackPanel>().Single().Children.OfType<Button>().Last().RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                    }));button("Review").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));await send.Task;
                    Window result=null;for(int attempt=0;attempt<700 && result==null;attempt++){await Task.Delay(150);result=window.OwnedWindows.Cast<Window>().FirstOrDefault(view=>view.Title.StartsWith("DSH 复核建议"));}
                    Require(result!=null && !label("LastReply").Contains("未报告"),"actual DSH default model receipt");result.DialogResult=false;await Task.Delay(100);
                    Require(label("Title")=="2 项需要修改" && demoCards.Children.Count==2,"review does not replace local errors");SaveView(window,"双模型错误清单");Console.WriteLine("Live 9B + DSH OK: "+label("LastReply"));local.IsChecked=false;
                }
                button("Back").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));Require(confirm.IsEnabled && confirm.IsChecked!=true && !button("Audit").IsEnabled,"fresh confirmation on return");
                string pasted="必交材料：\n1.创意赛道：\n请提交项目说明书（DOCX）。\n2.实践赛道：\n请提交演示视频（MP4）。";
                var pasteAction=window.Dispatcher.BeginInvoke(DispatcherPriority.ApplicationIdle,new Action(delegate {
                    var dialog=window.OwnedWindows.Cast<Window>().Single();var layout=(DockPanel)dialog.Content;
                    layout.Children.OfType<TextBox>().Single().Text=pasted;layout.Children.OfType<StackPanel>().Single().Children.OfType<Button>().Last().RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                }));
                button("Paste").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                await pasteAction.Task;
                for(int attempt=0;attempt<100 && label("Title")!="先选择适用范围";attempt++)await Task.Delay(30);
                var tracks=(ComboBox)window.FindName("Track");Require(tracks.Items.Count==2 && !button("Plan").IsEnabled && !confirm.IsEnabled,"paste dialog and track gate");
                tracks.SelectedIndex=0;await workbench.Generate();Require(confirm.IsEnabled,"first track plan");tracks.SelectedIndex=1;
                Require(!confirm.IsEnabled && !button("Audit").IsEnabled && button("Plan").IsEnabled,"switching track invalidates previous plan");
                Console.WriteLine("WPF OK: demo 0/2, read, confirm gate, audit 2, report, back, paste, switch tracks");code=0;
            }catch(Exception error){Console.Error.WriteLine(error);}finally{app.Shutdown();}
        }));app.Run(window);return code;
    }
}
