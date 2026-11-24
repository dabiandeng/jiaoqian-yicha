using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Markup;
using System.Windows.Media;
using Microsoft.Win32;
using Data = System.Collections.Generic.Dictionary<string,object>;
using Forms = System.Windows.Forms;

namespace Jiaoqian {
public static class Worker {
    public static readonly string Root=Path.GetDirectoryName(typeof(Worker).Assembly.Location);
    static string Runtime(out string prefix) {
        prefix="";string bundled=Path.Combine(Root,"runtime","python.exe");if(File.Exists(bundled))return bundled;
        foreach(var name in new[]{"python.exe","py.exe"})foreach(var folder in (Environment.GetEnvironmentVariable("PATH")??"").Split(';')) {
            if(!Path.IsPathRooted(folder) || folder.IndexOf("Microsoft\\WindowsApps",StringComparison.OrdinalIgnoreCase)>=0)continue;
            var file=Path.Combine(folder,name);if(File.Exists(file)){prefix=name=="py.exe"?"-3 ":"";return file;}
        }
        throw new InvalidOperationException("未找到 Python 运行时。请完整解压 Windows x64 便携版；源码用户请将 Python 加入 PATH。");
    }
    public static Data Call(Data request) {
        string prefix;var file=Runtime(out prefix);var json=new JavaScriptSerializer{MaxJsonLength=32*1024*1024};
        var info=new ProcessStartInfo(file,prefix+"-B -X utf8 gui_worker.py"){WorkingDirectory=Root,UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true,StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8};
        using(var child=Process.Start(info)) {
            var output=child.StandardOutput.ReadToEndAsync();var error=child.StandardError.ReadToEndAsync();
            var bytes=Encoding.UTF8.GetBytes(json.Serialize(request));child.StandardInput.BaseStream.Write(bytes,0,bytes.Length);child.StandardInput.Close();
            if(!child.WaitForExit(185000)){child.Kill();throw new TimeoutException("操作超时，本次未取得结果；不会自动重发。");}
            Task.WaitAll(output,error);if(child.ExitCode!=0)throw new InvalidOperationException("检查引擎未正常启动。请确认程序位于原项目目录且运行时可用。");
            var result=json.Deserialize<Data>(output.Result);if(!(bool)result["ok"])throw new InvalidOperationException((string)result["error"]);return (Data)result["data"];
        }
    }
}

public class Workbench {
    public Window Window {get; private set;}
    Data notice, plan, active, apiSettings;string folder="", report="", mode="home", apiLabel="";bool updating, busy;readonly HashSet<int> manual=new HashSet<int>();
    T Get<T>(string name) where T:class {return (T)Window.FindName(name);}
    Button Btn(string name){return Get<Button>(name);} TextBlock Label(string name){return Get<TextBlock>(name);} CheckBox Check(string name){return Get<CheckBox>(name);}
    static string Str(Data data,string name){return Convert.ToString(data[name]);}
    static IEnumerable<Data> Objects(Data data,string key){return ((IEnumerable)data[key]).Cast<Data>();}
    static Brush Color(string hex){return (Brush)new BrushConverter().ConvertFromString(hex);}
    static TextBlock Text(string value,double size,string color,bool bold=false){return new TextBlock{Text=value,FontSize=size,Foreground=Color(color),FontWeight=bold?FontWeights.SemiBold:FontWeights.Normal,TextWrapping=TextWrapping.Wrap};}
    public Workbench(){
        using(var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream("Main.xaml")){Window=(Window)XamlReader.Load(stream);}
        var screen=SystemParameters.WorkArea;Window.Width=Math.Min(Window.Width,screen.Width);Window.Height=Math.Min(Window.Height,screen.Height);
        Btn("PickNotice").Click+=async delegate {var dialog=new OpenFileDialog{Title="选择比赛通知",Filter="文字通知 (*.docx;*.txt;*.md)|*.docx;*.txt;*.md"};if(dialog.ShowDialog(Window)==true)await Read(new Data{{"path",dialog.FileName}});};
        Btn("Paste").Click+=async delegate {var text=PasteDialog();if(text!=null)await Read(new Data{{"text",text}});};
        Btn("PickFolder").Click+=delegate {using(var dialog=new Forms.FolderBrowserDialog{Description="选择要检查的提交文件夹（只读）",ShowNewFolderButton=false}){if(dialog.ShowDialog(new Owner(Window))==Forms.DialogResult.OK)SetFolder(dialog.SelectedPath);}};
        Btn("Demo").Click+=async delegate {await Demo();};Btn("Plan").Click+=async delegate {await Generate();};Btn("Audit").Click+=async delegate {await Audit();};Btn("Review").Click+=async delegate {await Review();};
        Btn("Report").Click+=delegate {try{if(File.Exists(report)&&Path.GetExtension(report)==".html")Process.Start(new ProcessStartInfo(report){UseShellExecute=true});else throw new IOException();}catch{MessageBox.Show(Window,"报告文件无法打开，请重新生成。","打开报告");}};
        Btn("Back").Click+=delegate {if(plan!=null)PaintPlan();};Get<ComboBox>("Track").SelectionChanged+=delegate {if(!updating)InvalidatePlan();};
        Btn("Inspect").Click+=delegate {InspectPlan();};Check("LocalPlan").Click+=delegate {InvalidatePlan();};Check("Confirm").Click+=delegate {Refresh();};
        Btn("Probe").Click+=async delegate {await Probe();};Window.Loaded+=async delegate {await Probe();};
        Btn("ConnectAi").Click+=delegate {ConfigureApi();};
        Home();Refresh();
    }
    class Owner:Forms.IWin32Window {public IntPtr Handle{get;private set;}public Owner(Window window){Handle=new System.Windows.Interop.WindowInteropHelper(window).Handle;}}
    void SetApi(Data settings,string caption){
        apiSettings=settings;apiLabel=caption;bool enabled=settings!=null;
        Label("ApiState").Text=enabled?"初读 / 复核："+caption+" · 已通过测试（仅本次）":"";
        Label("ApiState").Visibility=enabled?Visibility.Visible:Visibility.Collapsed;
        foreach(string name in new[]{"LocalState","DshState","Probe"})((UIElement)Window.FindName(name)).Visibility=enabled?Visibility.Collapsed:Visibility.Visible;
        Btn("ConnectAi").Content=enabled?"管理我的 AI…":"接入我的 AI…";
        Check("LocalPlan").Content=enabled?"用我的 API 识别材料（取消则仅用规则）":"用本机 9B 识别材料（取消则仅用规则）";
    }
    void ConfigureApi(){
        DockPanel panel;StackPanel buttons;var dialog=Dialog("接入我的 AI · 仅本次使用",510,out panel,out buttons);
        var form=new StackPanel();panel.Children.Add(new ScrollViewer{Content=form,VerticalScrollBarVisibility=ScrollBarVisibility.Auto});
        form.Children.Add(Text("选择接口",16,"#193D36",true));var provider=new ComboBox{Margin=new Thickness(0,6,0,10)};
        provider.Items.Add("DeepSeek 官方 API");provider.Items.Add("其他兼容接口（Chat Completions）");form.Children.Add(provider);
        bool custom=apiSettings!=null && (bool)apiSettings["custom"];var fields=new StackPanel();form.Children.Add(fields);
        fields.Children.Add(Text("基础地址（含版本路径，如 https://你的服务/v1）",12,"#345046"));var address=new TextBox{Text=custom?Str(apiSettings,"base"):"",Margin=new Thickness(0,3,0,8)};fields.Children.Add(address);
        fields.Children.Add(Text("模型 ID（按服务商提供的名称填写）",12,"#345046"));var model=new TextBox{Text=custom?Str(apiSettings,"model"):"",Margin=new Thickness(0,3,0,8)};fields.Children.Add(model);
        var destination=Text("",12,"#345046");form.Children.Add(destination);form.Children.Add(Text("API Key（不保存；无鉴权的兼容接口可留空）",12,"#345046"));
        var key=new PasswordBox{Margin=new Thickness(0,3,0,10)};form.Children.Add(key);
        var info=Text("只向所选接口发送固定测试文本，不发送通知或作品。可能产生少量费用。请只向可信服务输入该服务的密钥。",13,"#4B6256");form.Children.Add(info);
        provider.SelectionChanged+=delegate{bool other=provider.SelectedIndex==1;fields.Visibility=other?Visibility.Visible:Visibility.Collapsed;destination.Text=other?"请核对上方接口地址，仅本机允许 HTTP。":"服务地址：api.deepseek.com";key.Clear();};
        address.TextChanged+=delegate{key.Clear();};provider.SelectedIndex=custom?1:0;if(apiSettings!=null)key.Password=Str(apiSettings,"key");
        if(apiSettings!=null){var remove=new Button{Content="断开 API",Margin=new Thickness(0,0,8,0)};buttons.Children.Add(remove);remove.Click+=delegate{SetApi(null,"");dialog.DialogResult=true;};}
        var test=new Button{Content="测试连接并使用",Style=(Style)Window.Resources["Primary"]};buttons.Children.Add(test);
        test.Click+=async delegate{
            bool other=provider.SelectedIndex==1;var draft=new Data{{"custom",other},{"base",other?address.Text.Trim():""},{"model",other?model.Text.Trim():""},{"key",key.Password.Trim()}};
            if((!other && Str(draft,"key")=="") || (other && (Str(draft,"base")==""||Str(draft,"model")==""))){info.Text="请先填好所需信息。";return;}
            form.IsEnabled=false;buttons.IsEnabled=false;info.Text="正在测试接口和 JSON 回复……";
            try{var request=new Data(draft){{"action","test_api"}};var result=await Task.Run(()=>Worker.Call(request));
                if(!dialog.IsVisible)return;string host=other?new Uri(Str(draft,"base")).Authority:"api.deepseek.com";SetApi(draft,host+" / "+Str(result,"model"));dialog.DialogResult=true;
            }catch(Exception exc){info.Text=exc.Message;}finally{form.IsEnabled=true;buttons.IsEnabled=true;}
        };dialog.ShowDialog();key.Clear();
    }
    async Task Probe(){if(!Btn("Probe").IsEnabled)return;Btn("Probe").IsEnabled=false;
        try{var status=await Task.Run(()=>Worker.Call(new Data{{"action","connections"}}));Label("LocalState").Text="初读 / "+status["local"];Label("DshState").Text="复核 / "+status["dsh"];}
        catch{Label("LocalState").Text="初读 / 未取得连接状态";Label("DshState").Text="复核 / 未取得连接状态";}finally{Btn("Probe").IsEnabled=true;}}
    void Refresh(){
        var track=Get<ComboBox>("Track");Btn("Plan").IsEnabled=notice!=null && (track.Items.Count==0||track.SelectedIndex>=0);
        Check("Confirm").IsEnabled=plan!=null&&mode=="plan";Btn("Audit").IsEnabled=Check("Confirm").IsEnabled&&Check("Confirm").IsChecked==true&&folder.Length>0;
        Btn("Inspect").IsEnabled=Check("Confirm").IsEnabled;
        int pending=active==null?0:Objects(active,"options").Count();Btn("Review").Visibility=pending>0?Visibility.Visible:Visibility.Collapsed;Btn("Review").Content="人工复核（"+pending+" 段）…";Btn("Report").Visibility=report.Length>0?Visibility.Visible:Visibility.Collapsed;
        Btn("Back").Visibility=plan!=null&&mode!="plan"?Visibility.Visible:Visibility.Collapsed;
    }
    async Task Run(string status,Data request,Action<Data> done){
        if(busy)return;busy=true;Get<Grid>("Body").IsEnabled=false;Btn("Demo").IsEnabled=false;Label("Status").Text=status;
        try {var result=await Task.Run(()=>Worker.Call(request));Label("Status").Text="已完成 · 作品原件未修改";done(result);}
        catch(Exception exc){Label("Status").Text="未完成 · "+exc.Message;MessageBox.Show(Window,exc.Message,"交前一查",MessageBoxButton.OK,MessageBoxImage.Information);}
        finally{busy=false;Get<Grid>("Body").IsEnabled=true;Btn("Demo").IsEnabled=true;Refresh();}
    }
    void Header(string view,string kicker,string badge,string title,string subtitle){mode=view;Label("Kicker").Text=kicker;Label("Badge").Text=badge;Label("Title").Text=title;Label("Subtitle").Text=subtitle;Get<StackPanel>("Cards").Children.Clear();Get<ScrollViewer>("Scroll").ScrollToTop();report="";}
    StackPanel Card(string eyebrow,string title,string body,string tone){
        string bg=tone=="warn"?"#FFF3E9":tone=="green"?"#E8F2E9":"#F2F4EF";
        var stack=new StackPanel();stack.Children.Add(Text(eyebrow,11,tone=="warn"?"#985020":"#47745C",true));var headline=Text(title,17,"#203D33",true);headline.Margin=new Thickness(0,3,0,2);stack.Children.Add(headline);
        if(body.Length>0)stack.Children.Add(Text(body,13,"#4B6256"));Get<StackPanel>("Cards").Children.Add(new Border{Background=Color(bg),CornerRadius=new CornerRadius(8),Padding=new Thickness(13,10,13,11),Margin=new Thickness(0,0,3,8),Child=stack});return stack;
    }
    void Home(){active=null;Header("home","WORKSPACE   /   预检工作区","未开始","从一份比赛通知开始","每一条自动判定都能回到通知里的原句。");
        Card("01  /  规则","先看这场比赛到底要交什么","导入主办方通知；有多个赛道时，先选适用赛道。","green");
        Card("02  /  证据","再看文件哪里不符合要求","核对缺件、格式、大小、PPT 页数或可读取的视频时长。","neutral");
        Card("人工边界","读不准的，不替你判通过","内容、字幕、原创性和不可靠的页数留给人看。想先体验？点右上角的两赛对照。","neutral");
    }
    void InvalidatePlan(){plan=null;active=null;manual.Clear();Check("Confirm").IsChecked=false;
        if(notice==null)Home();else{var track=Get<ComboBox>("Track");bool choose=track.Items.Count>0 && track.SelectedIndex<0;
            Header("ready","SOURCE   /   比赛通知","待生成",choose?"先选择适用范围":"通知已就绪",Str(notice,"title"));
            Card("下一步",choose?"选择范围，再生成任务清单":"点击左侧「生成任务清单」",choose?"只采用所选赛道、阶段或主题的材料要求。":"先看程序提取了哪些要求，再决定是否用它们检查文件。","green");
            Label("Status").Text=choose?"等待选择适用范围":"等待生成任务清单";}Refresh();}
    public async Task Read(Data input){if(busy)return;notice=null;Label("NoticeLabel").Text="尚未选择通知";Label("NoticeLabel").ToolTip=null;Get<StackPanel>("TrackBox").Visibility=Visibility.Collapsed;InvalidatePlan();input["action"]="read";await Run("正在读取通知……",input, result=>{
        notice=result;Label("NoticeLabel").Text=Str(notice,"title");Label("NoticeLabel").ToolTip=Label("NoticeLabel").Text;updating=true;
        var track=Get<ComboBox>("Track");track.Items.Clear();foreach(string item in (IEnumerable)notice["tracks"])track.Items.Add(item);track.SelectedIndex=track.Items.Count==1?0:-1;Get<StackPanel>("TrackBox").Visibility=track.Items.Count>0?Visibility.Visible:Visibility.Collapsed;updating=false;InvalidatePlan();});}
    public void SetFolder(string path){folder=path;Label("FolderLabel").Text=path;Label("FolderLabel").ToolTip=path;if(mode=="audit"||mode=="review"){Check("Confirm").IsChecked=false;PaintPlan();}Refresh();}
    public async Task Generate(){if(notice==null)return;var request=new Data{{"action","plan"},{"text",notice["text"]},{"title",notice["title"]},{"track",Get<ComboBox>("Track").SelectedItem},{"local",Check("LocalPlan").IsChecked==true}};
        if(apiSettings!=null && (bool)request["local"]){
            if(MessageBox.Show(Window,"发送候选通知段落至 "+apiLabel+"？可能包含整份通知（最多 9000 字），不发送参赛作品。取消勾选可仅用规则。","确认 API 初读",MessageBoxButton.OKCancel,MessageBoxImage.Question,MessageBoxResult.Cancel)!=MessageBoxResult.OK)return;
            request["local"]=false;request["cloud"]=true;request["consent"]=true;foreach(var entry in apiSettings)request[entry.Key]=entry.Value;
        }
        InvalidatePlan();
        await Run("正在生成有原文依据的任务清单……",request,result=>{plan=result;manual.Clear();Check("Confirm").IsChecked=false;PaintPlan();if(Str(result,"warning").Length>0)Label("Status").Text=Str(result,"warning");});}
    void PaintPlan(){active=plan;int count=Objects(plan,"items").Count();Header("plan","CHECKLIST   /   任务清单","待核对",count==0?"未识别到可检查的材料":"任务清单已生成",Str(plan,"method")+" · "+count+" 项材料。先点「核对规则」，核对后再扫描；未归类原句在人工复核页。 ");
        report=Str(plan,"planFile");Btn("Report").Content="打开完整清单 ↗";Refresh();}
    void InspectPlan(){if(plan==null)return;DockPanel panel;StackPanel buttons;var dialog=Dialog("核对规则 · 任务清单",570,out panel,out buttons);((Button)buttons.Children[0]).Content="返回";
        var list=new StackPanel();panel.Children.Add(new ScrollViewer{Content=list,VerticalScrollBarVisibility=ScrollBarVisibility.Auto});int pos=0;
        foreach(var item in Objects(plan,"items")){int index=++pos;var label=Text("材料 "+index.ToString("00")+"　"+Str(item,"label"),16,"#193D36",true);label.Margin=new Thickness(0,10,0,2);list.Children.Add(label);list.Children.Add(Text(Str(item,"rule"),13,"#345046"));list.Children.Add(Text("依据 #"+item["source"]+"　"+item["quote"],12,"#506659"));var choice=new CheckBox{Content="此项改为人工核查",IsChecked=(bool)item["manual"]||manual.Contains(index),IsEnabled=!(bool)item["manual"],Margin=new Thickness(0,5,0,5)};choice.Click+=delegate{if(choice.IsChecked==true)manual.Add(index);else manual.Remove(index);Check("Confirm").IsChecked=false;Refresh();};list.Children.Add(choice);}
        if(pos==0)list.Children.Add(Text("没有自动材料。请核对原文，或仅导入提交材料段。",14,"#794324"));dialog.ShowDialog();}
    public async Task Audit(){if(!Btn("Audit").IsEnabled)return;await Run("正在只读检查文件夹……",new Data{{"action","audit"},{"directory",plan["directory"]},{"folder",folder},{"confirmed",true},{"manual",string.Join(",",manual)}},result=>{
        active=result;var rows=Objects(result,"rows").ToArray();var failures=rows.Where(row=>Str(row,"status")=="需修改").ToArray();bool tested=rows.Any(row=>Str(row,"status")=="通过"||Str(row,"status")=="需修改");
        Header("audit","ERRORS   /   错误清单","本地实测",failures.Length>0?failures.Length+" 项需要修改":tested?"已检查项未发现错误":"没有可自动判定的结果",Str(result,"title")+" · "+(tested?"只列已确认的错误，不代表整份作品合格。":"本次没有得出通过或不通过的结论。"));
        foreach(var row in failures)Finding(row);
        report=Str(result,"file");Btn("Report").Content="打开全部判定与依据 ↗";Check("Confirm").IsChecked=false;});}
    void Finding(Data row){var card=Card(Str(row,"status")+"   /   "+row["material"],Str(row,"key")+"："+row["value"],Str(row,"action"),Str(row,"status")=="需修改"?"warn":"neutral");card.Children.Add(Text("依据 #"+row["source"]+"　"+row["quote"],12,"#506659"));}
    public async Task Demo(){await Run("正在用同一个样包执行两次离线预检……",new Data{{"action","demo"}},result=>{
        active=null;Check("Confirm").IsChecked=false;Header("demo","LIVE DEMO   /   两赛对照","实时离线","同一文件夹，两种结果","样包不变，只替换比赛通知。以下是本次运行结果，不是截图。");
        foreach(var item in Objects(result,"cases")){var failures=Objects(item,"failures").ToArray();var card=Card(Str(item,"label"),failures.Length==0?"未发现硬性错误":failures.Length+" 项需要修改",failures.Length==0?"仍需人工核对内容和未测要求。":"以下读数不满足这份通知的要求。",failures.Length==0?"green":"warn");foreach(var row in failures)card.Children.Add(Text("• "+row["key"]+"："+row["value"]+"　/　"+row["action"],13,"#674C32"));}
        report=Str(result,"file");Btn("Report").Content="打开两份报告 ↗";});}

    Window Dialog(string title,double height,out DockPanel panel,out StackPanel buttons){var dialog=new Window{Title=title,Owner=Window,WindowStartupLocation=WindowStartupLocation.CenterOwner,Width=Math.Min(620,Window.ActualWidth),Height=Math.Min(height,Math.Max(400,Window.ActualHeight)),FontFamily=Window.FontFamily,Background=Color("#F4F5F0"),ShowInTaskbar=false,Resources=Window.Resources};panel=new DockPanel{Margin=new Thickness(18)};dialog.Content=panel;buttons=new StackPanel{Orientation=Orientation.Horizontal,HorizontalAlignment=HorizontalAlignment.Right,Margin=new Thickness(0,10,0,0)};DockPanel.SetDock(buttons,Dock.Bottom);panel.Children.Add(buttons);var cancel=new Button{Content="取消",IsCancel=true,Margin=new Thickness(0,0,8,0)};buttons.Children.Add(cancel);return dialog;}
    string PasteDialog(){DockPanel panel;StackPanel buttons;var dialog=Dialog("粘贴比赛通知正文",430,out panel,out buttons);var field=new TextBox{AcceptsReturn=true,TextWrapping=TextWrapping.Wrap,MaxLength=80000,Padding=new Thickness(8),VerticalScrollBarVisibility=ScrollBarVisibility.Auto};panel.Children.Add(field);var submit=new Button{Content="使用这份通知",Style=(Style)Window.Resources["Primary"]};submit.Click+=delegate{if(!string.IsNullOrWhiteSpace(field.Text))dialog.DialogResult=true;};buttons.Children.Add(submit);dialog.Loaded+=delegate{field.Focus();};return dialog.ShowDialog()==true?field.Text:null;}
    async Task Review(){
        if(active==null)return;var target=active;DockPanel panel;StackPanel buttons;var dialog=Dialog("人工复核 · 原句与证据",610,out panel,out buttons);
        ((Button)buttons.Children[0]).Content="返回";
        string provider=apiSettings==null?"DSH":"API";var note=Text("下列条款未作自动结论。可自行对照，也可让 "+(apiSettings==null?"DSH 默认模型":apiLabel)+" 给建议。只发送所选原句与类别，不发送作品、文件名或路径。最多 12 段、6000 字，可能按模型账户计费。",13,"#4B6256");note.Margin=new Thickness(0,0,0,12);DockPanel.SetDock(note,Dock.Top);panel.Children.Add(note);
        var list=new StackPanel();panel.Children.Add(new ScrollViewer{Content=list,VerticalScrollBarVisibility=ScrollBarVisibility.Auto});var boxes=new List<CheckBox>();
        var findings=(target.ContainsKey("rows")?Objects(target,"rows"):Enumerable.Empty<Data>()).Where(row=>Str(row,"status")=="人工核查" && Str(row,"material")!="通知其余原文").ToLookup(row=>Convert.ToInt32(row["source"]));
        var advice=target.ContainsKey("advice")?(Dictionary<int,string>)target["advice"]:new Dictionary<int,string>();target["advice"]=advice;
        foreach(var option in Objects(target,"options").OrderBy(option=>string.Join("；",((IEnumerable)option["focus"]).Cast<string>())=="未归类原文")){
            int id=Convert.ToInt32(option["id"]);var block=new StackPanel();block.Children.Add(Text("原文 #"+id+"　"+string.Join("；",((IEnumerable)option["focus"]).Cast<string>()),11,"#557360",true));
            block.Children.Add(Text(Str(option,"quote"),14,"#193D36"));foreach(var row in findings[id])block.Children.Add(Text(row["material"]+" / "+row["key"]+"："+row["value"],12,"#53695C"));
            if(advice.ContainsKey(id))block.Children.Add(Text("复核建议："+advice[id],13,"#235E49"));
            var box=new CheckBox{Tag=id,Content=block,Margin=new Thickness(2,4,8,9),HorizontalContentAlignment=HorizontalAlignment.Stretch};boxes.Add(box);list.Children.Add(box);
        }
        var submit=new Button{Content="同意发送所选原句给 "+provider,Style=(Style)Window.Resources["Primary"],IsEnabled=false};foreach(var box in boxes)box.Click+=delegate{int count=boxes.Count(item=>item.IsChecked==true);submit.IsEnabled=count>0&&count<=12;};submit.Click+=delegate{dialog.DialogResult=true;};buttons.Children.Add(submit);if(dialog.ShowDialog()!=true)return;
        var request=new Data{{"action","review"},{"directory",target["directory"]},{"ids",string.Join(",",boxes.Where(box=>box.IsChecked==true).Select(box=>box.Tag))},{"consent",true},{"provider",provider}};if(apiSettings!=null)foreach(var entry in apiSettings)request[entry.Key]=entry.Value;
        await Run("等待 "+provider+" 复核；本地结果不变……",request,result=>{
            string model=Str(result,"model");string via=string.IsNullOrEmpty(model)?"未报告实际型号":Str(result,"provider")+" / "+model;
            Label("LastReply").Text="上次复核回执 / "+via;Label("LastReply").Visibility=Visibility.Visible;
            DockPanel content;StackPanel actions;var details=Dialog(provider+" 复核建议 · 不改变自动结论",480,out content,out actions);((Button)actions.Children[0]).Content="返回";
            var answers=new StackPanel();content.Children.Add(new ScrollViewer{Content=answers,VerticalScrollBarVisibility=ScrollBarVisibility.Auto});answers.Children.Add(Text("本次回执："+via,12,"#557360"));
            foreach(var item in Objects(result,"items")){int id=Convert.ToInt32(item["id"]);advice[id]=Str(item,"action");var quote=Text("原文 #"+id+"　"+item["evidence"],14,"#193D36",true);quote.Margin=new Thickness(0,12,0,3);answers.Children.Add(quote);answers.Children.Add(Text(advice[id],14,"#365E4D"));}details.ShowDialog();});
    }
    [STAThread] public static void Main(){try{var app=new Application();var ui=new Workbench();app.Run(ui.Window);}catch(Exception exc){MessageBox.Show("图形预览无法启动："+exc.Message,"交前一查");}}
}
}
