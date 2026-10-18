"""The LENTA Android app's code, as Dalvik instructions for dexwriter.

What the app does (it is a full-screen window onto your LENTA server):
  MainActivity  WebView with JavaScript, DOM storage, cookies; unmuted autoplay; downloads through Android's
                Download manager; Back goes back in LENTA; full-screen video; file picker for artwork uploads.
  Client        keeps LENTA pages in the app and opens other sites (IMDb, Wikipedia…) in the browser;
                shows the "can't reach the server" page when the server doesn't answer.
  Chrome        full-screen video (landscape), no grey default video picture, and the file picker.
  Bridge        window.LentaApp for pages: setServer (only from the app's own setup page), server(),
                changeServer(), keepScreenOn(on), audioPlaying(on), setColors(argb), fullscreen(on), version().
  Ui            runs Bridge requests on the main thread.

HTTPS: LENTA's own certificate (issued by "LENTA local CA") is accepted for the saved server's address (and on
the setup page while you connect); any other certificate problem shows the setup page with an explanation.
Music and audiobooks keep playing with the screen off (a partial wake lock while they play). When LENTA
moves to another port or to https on the same machine, the app remembers the new address by itself.
"""
from dexwriter import ACC_PUBLIC, ACC_VOLATILE, ClassDef, Field, Method

VERSION = "1.1.5"
PKG = "app.lenta"
MA, CL, CH, BR, UI = "Lapp/lenta/MainActivity;", "Lapp/lenta/Client;", "Lapp/lenta/Chrome;", "Lapp/lenta/Bridge;", "Lapp/lenta/Ui;"
ACT = "Landroid/app/Activity;"
WV = "Landroid/webkit/WebView;"
STR = "Ljava/lang/String;"
OBJ = "Ljava/lang/Object;"
JSI = "Landroid/webkit/JavascriptInterface;"
CVC = "Landroid/webkit/WebChromeClient$CustomViewCallback;"
VCB = "Landroid/webkit/ValueCallback;"
FCP = "Landroid/webkit/WebChromeClient$FileChooserParams;"
REQ = "Landroid/webkit/WebResourceRequest;"
DMR = "Landroid/app/DownloadManager$Request;"
SP = "Landroid/content/SharedPreferences;"
WL = "Landroid/os/PowerManager$WakeLock;"
SSLE = "Landroid/net/http/SslError;"
SSLH = "Landroid/webkit/SslErrorHandler;"
SSLC = "Landroid/net/http/SslCertificate;"
DN = "Landroid/net/http/SslCertificate$DName;"
SPE = "Landroid/content/SharedPreferences$Editor;"
SETUP = "file:///android_asset/setup.html"
BG = 0xFF0A0A0C                   # LENTA's near-black
FILE_REQUEST = 7

F_WEB, F_CUSTOM, F_CB, F_FILE, F_HOST, F_SETUP = (f"{MA}->web:{WV}", f"{MA}->custom:Landroid/view/View;",
                                                  f"{MA}->customCb:{CVC}", f"{MA}->fileCb:{VCB}",
                                                  f"{MA}->host:{STR}", f"{MA}->onSetup:Z")
F_WL = f"{MA}->wl:{WL}"
LP = "Landroid/view/WindowManager$LayoutParams;"
F_VIDEO, F_PIP, F_FULL = f"{MA}->videoOn:Z", f"{MA}->wasPip:Z", f"{MA}->full:Z"
PIPB = "Landroid/app/PictureInPictureParams$Builder;"
UI_INIT = f"{UI}-><init>({MA}IZI)V"


def L(name):
    return ("label", name)


def prefs(dst, act, tmp):
    """dst = act.getSharedPreferences("lenta", 0)"""
    return [("const-string", dst, "lenta"), ("const/4", tmp, 0),
            ("invoke-virtual", [act, dst, tmp], f"{ACT}->getSharedPreferences({STR}I){SP}"),
            ("move-result-object", dst)]


def ctor_with_activity(cls, super_):
    return Method("<init>", f"({MA})V", regs=2, code=[
        ("invoke-direct", ["v0"], f"{super_}-><init>()V"),
        ("iput-object", "v1", "v0", f"{cls}->a:{MA}"),
        ("return-void",)])


def classes() -> list[ClassDef]:
    main = ClassDef(MA, ACT, interfaces=["Landroid/webkit/DownloadListener;"], fields=[
        Field("web", WV), Field("custom", "Landroid/view/View;"), Field("customCb", CVC), Field("fileCb", VCB),
        Field("host", STR), Field("onSetup", "Z", ACC_PUBLIC | ACC_VOLATILE), Field("wl", WL),
        Field("videoOn", "Z"), Field("wasPip", "Z"), Field("full", "Z")], methods=[
        Method("<init>", "()V", regs=1, code=[
            ("invoke-direct", ["v0"], f"{ACT}-><init>()V"), ("return-void",)]),

        # onCreate: window colours, the WebView and its settings, then start()
        Method("onCreate", "(Landroid/os/Bundle;)V", regs=8, code=[
            ("invoke-super", ["v6", "v7"], f"{ACT}->onCreate(Landroid/os/Bundle;)V"),
            ("invoke-virtual", ["v6"], f"{ACT}->getWindow()Landroid/view/Window;"),
            ("move-result-object", "v0"),
            ("const", "v1", BG),
            ("invoke-virtual", ["v0", "v1"], "Landroid/view/Window;->setStatusBarColor(I)V"),
            ("invoke-virtual", ["v0", "v1"], "Landroid/view/Window;->setNavigationBarColor(I)V"),
            # The window is painted before the WebView has anything to show, and the theme LENTA uses
            # (Theme.Material.NoActionBar) paints it the Material grey. So every cold start flashed a
            # grey screen for as long as the page took to draw — on a phone, long enough to see every
            # time. Give the window LENTA's own near-black and the start is black all the way through.
            ("new-instance", "v2", "Landroid/graphics/drawable/ColorDrawable;"),
            ("invoke-direct", ["v2", "v1"], "Landroid/graphics/drawable/ColorDrawable;-><init>(I)V"),
            ("invoke-virtual", ["v0", "v2"],
             "Landroid/view/Window;->setBackgroundDrawable(Landroid/graphics/drawable/Drawable;)V"),
            ("new-instance", "v0", WV),
            ("invoke-direct", ["v0", "v6"], f"{WV}-><init>(Landroid/content/Context;)V"),
            ("iput-object", "v0", "v6", F_WEB),
            ("invoke-virtual", ["v0", "v1"], f"{WV}->setBackgroundColor(I)V"),
            ("invoke-virtual", ["v0"], f"{WV}->getSettings()Landroid/webkit/WebSettings;"),
            ("move-result-object", "v2"),
            ("const/4", "v3", 1),
            ("invoke-virtual", ["v2", "v3"], "Landroid/webkit/WebSettings;->setJavaScriptEnabled(Z)V"),
            ("invoke-virtual", ["v2", "v3"], "Landroid/webkit/WebSettings;->setDomStorageEnabled(Z)V"),
            ("const/4", "v4", 0),
            ("invoke-virtual", ["v2", "v4"], "Landroid/webkit/WebSettings;->setMediaPlaybackRequiresUserGesture(Z)V"),
            # honour the page's <meta name="viewport"> like Chrome does; without it the WebView widens the
            # page to its content, and full-height layouts and the bottom tab bar end up off screen
            ("invoke-virtual", ["v2", "v3"], "Landroid/webkit/WebSettings;->setUseWideViewPort(Z)V"),
            ("invoke-virtual", ["v2", "v4"], "Landroid/webkit/WebSettings;->setLoadWithOverviewMode(Z)V"),
            ("invoke-virtual", ["v2", "v4"], "Landroid/webkit/WebSettings;->setSupportZoom(Z)V"),
            ("invoke-virtual", ["v2"], f"Landroid/webkit/WebSettings;->getUserAgentString(){STR}"),
            ("move-result-object", "v4"),
            ("const-string", "v5", f" LENTA-Android/{VERSION}"),
            ("invoke-virtual", ["v4", "v5"], f"{STR}->concat({STR}){STR}"),
            ("move-result-object", "v4"),
            ("invoke-virtual", ["v2", "v4"], f"Landroid/webkit/WebSettings;->setUserAgentString({STR})V"),
            ("invoke-static", [], "Landroid/webkit/CookieManager;->getInstance()Landroid/webkit/CookieManager;"),
            ("move-result-object", "v2"),
            ("invoke-virtual", ["v2", "v3"], "Landroid/webkit/CookieManager;->setAcceptCookie(Z)V"),
            ("invoke-virtual", ["v2", "v0", "v3"], f"Landroid/webkit/CookieManager;->setAcceptThirdPartyCookies({WV}Z)V"),
            ("new-instance", "v2", CL),
            ("invoke-direct", ["v2", "v6"], f"{CL}-><init>({MA})V"),
            ("invoke-virtual", ["v0", "v2"], f"{WV}->setWebViewClient(Landroid/webkit/WebViewClient;)V"),
            ("new-instance", "v2", CH),
            ("invoke-direct", ["v2", "v6"], f"{CH}-><init>({MA})V"),
            ("invoke-virtual", ["v0", "v2"], f"{WV}->setWebChromeClient(Landroid/webkit/WebChromeClient;)V"),
            ("invoke-virtual", ["v0", "v6"], f"{WV}->setDownloadListener(Landroid/webkit/DownloadListener;)V"),
            ("new-instance", "v2", BR),
            ("invoke-direct", ["v2", "v6"], f"{BR}-><init>({MA})V"),
            ("const-string", "v4", "LentaApp"),
            ("invoke-virtual", ["v0", "v2", "v4"], f"{WV}->addJavascriptInterface({OBJ}{STR})V"),
            ("invoke-virtual", ["v6", "v0"], f"{ACT}->setContentView(Landroid/view/View;)V"),
            ("invoke-virtual", ["v6"], f"{MA}->start()V"),
            ("return-void",)]),

        # start: the saved server, or the setup page on first launch
        Method("start", "()V", regs=5, code=[
            *prefs("v0", "v4", "v1"),
            ("const-string", "v1", "server"),
            ("const/4", "v2", 0),
            ("invoke-interface", ["v0", "v1", "v2"], f"{SP}->getString({STR}{STR}){STR}"),
            ("move-result-object", "v0"),
            ("iget-object", "v1", "v4", F_WEB),
            ("if-nez", "v0", "has"),
            ("const-string", "v2", SETUP),
            ("invoke-virtual", ["v1", "v2"], f"{WV}->loadUrl({STR})V"),
            ("return-void",),
            L("has"),
            ("invoke-static", ["v0"], f"Landroid/net/Uri;->parse({STR})Landroid/net/Uri;"),
            ("move-result-object", "v2"),
            ("invoke-virtual", ["v2"], f"Landroid/net/Uri;->getHost(){STR}"),
            ("move-result-object", "v2"),
            ("iput-object", "v2", "v4", F_HOST),
            ("invoke-virtual", ["v1", "v0"], f"{WV}->loadUrl({STR})V"),
            ("return-void",)]),

        Method("openSetup", "()V", regs=3, code=[
            ("iget-object", "v0", "v2", F_WEB),
            ("const-string", "v1", SETUP + "?change=1"),
            ("invoke-virtual", ["v0", "v1"], f"{WV}->loadUrl({STR})V"),
            ("return-void",)]),

        # keepOn: keep the screen awake while a video plays
        Method("keepOn", "(Z)V", regs=4, code=[
            ("iput-boolean", "v3", "v2", F_VIDEO),     # a video is playing (Home then shrinks it to picture-in-picture)
            ("invoke-virtual", ["v2"], f"{ACT}->getWindow()Landroid/view/Window;"),
            ("move-result-object", "v0"),
            ("const/16", "v1", 128),                 # WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
            ("if-eqz", "v3", "off"),
            ("invoke-virtual", ["v0", "v1"], "Landroid/view/Window;->addFlags(I)V"),
            ("return-void",),
            L("off"),
            ("invoke-virtual", ["v0", "v1"], "Landroid/view/Window;->clearFlags(I)V"),
            ("return-void",)]),

        # audio: music or an audiobook is playing — keep the CPU awake so it goes on with the screen off
        Method("audio", "(Z)V", regs=5, code=[
            ("iget-object", "v0", "v3", F_WL),
            ("if-nez", "v0", "have"),
            ("const-string", "v1", "power"),          # Context.POWER_SERVICE
            ("invoke-virtual", ["v3", "v1"], f"{ACT}->getSystemService({STR}){OBJ}"),
            ("move-result-object", "v1"),
            ("check-cast", "v1", "Landroid/os/PowerManager;"),
            ("const/4", "v2", 1),                     # PARTIAL_WAKE_LOCK
            ("const-string", "v0", "lenta:audio"),
            ("invoke-virtual", ["v1", "v2", "v0"], f"Landroid/os/PowerManager;->newWakeLock(I{STR}){WL}"),
            ("move-result-object", "v0"),
            ("const/4", "v2", 0),
            ("invoke-virtual", ["v0", "v2"], f"{WL}->setReferenceCounted(Z)V"),
            ("iput-object", "v0", "v3", F_WL),
            L("have"),
            ("invoke-virtual", ["v0"], f"{WL}->isHeld()Z"),
            ("move-result", "v1"),
            ("if-eqz", "v4", "off"),
            ("if-nez", "v1", "end"),
            ("invoke-virtual", ["v0"], f"{WL}->acquire()V"),
            ("return-void",),
            L("off"),
            ("if-eqz", "v1", "end"),
            ("invoke-virtual", ["v0"], f"{WL}->release()V"),
            L("end"),
            ("return-void",)]),

        # colors: the status and navigation bars take the colour of the theme in use
        Method("colors", "(I)V", regs=4, code=[
            ("invoke-virtual", ["v2"], f"{ACT}->getWindow()Landroid/view/Window;"),
            ("move-result-object", "v0"),
            ("invoke-virtual", ["v0", "v3"], "Landroid/view/Window;->setStatusBarColor(I)V"),
            ("invoke-virtual", ["v0", "v3"], "Landroid/view/Window;->setNavigationBarColor(I)V"),
            ("iget-object", "v1", "v2", F_WEB),
            ("invoke-virtual", ["v1", "v3"], f"{WV}->setBackgroundColor(I)V"),
            ("return-void",)]),

        # Home (or another app) while a video plays: it goes on in a small window (picture-in-picture)
        Method("onUserLeaveHint", "()V", regs=5, code=[
            ("invoke-super", ["v4"], f"{ACT}->onUserLeaveHint()V"),
            ("iget-boolean", "v0", "v4", F_VIDEO),
            ("if-eqz", "v0", "end"),
            ("new-instance", "v0", PIPB),
            ("invoke-direct", ["v0"], f"{PIPB}-><init>()V"),
            ("new-instance", "v1", "Landroid/util/Rational;"),
            ("const/16", "v2", 16),
            ("const/16", "v3", 9),
            ("invoke-direct", ["v1", "v2", "v3"], "Landroid/util/Rational;-><init>(II)V"),
            ("invoke-virtual", ["v0", "v1"], f"{PIPB}->setAspectRatio(Landroid/util/Rational;){PIPB}"),
            ("move-result-object", "v0"),
            ("invoke-virtual", ["v0"], f"{PIPB}->build()Landroid/app/PictureInPictureParams;"),
            ("move-result-object", "v0"),
            ("invoke-virtual", ["v4", "v0"], f"{ACT}->enterPictureInPictureMode(Landroid/app/PictureInPictureParams;)Z"),
            L("end"),
            ("return-void",)]),

        # the page learns about picture-in-picture: it shows only the video while the window is small
        Method("onPictureInPictureModeChanged", "(ZLandroid/content/res/Configuration;)V", regs=6, code=[
            ("invoke-super", ["v3", "v4", "v5"], f"{ACT}->onPictureInPictureModeChanged(ZLandroid/content/res/Configuration;)V"),
            ("const-string", "v0", "document.documentElement.classList.remove('pip')"),
            ("if-eqz", "v4", "js"),
            ("const/4", "v1", 1),
            ("iput-boolean", "v1", "v3", F_PIP),
            ("const-string", "v0", "document.documentElement.classList.add('pip')"),
            L("js"),
            ("iget-object", "v1", "v3", F_WEB),
            ("const/4", "v2", 0),
            ("invoke-virtual", ["v1", "v0", "v2"], f"{WV}->evaluateJavascript({STR}{VCB})V"),
            ("return-void",)]),

        # back in the app (also from picture-in-picture): nothing to stop
        Method("onResume", "()V", regs=2, code=[
            ("invoke-super", ["v1"], f"{ACT}->onResume()V"),
            ("const/4", "v0", 0),
            ("iput-boolean", "v0", "v1", F_PIP),
            ("iget-boolean", "v0", "v1", F_FULL),
            ("if-eqz", "v0", "end"),
            ("invoke-virtual", ["v1"], f"{MA}->applyFull()V"),      # back from the small window: bars hidden again
            L("end"),
            ("return-void",)]),

        # the video player asks for the whole screen: sideways, status and navigation bars hidden.
        # Native, so it needs no tap first (a web page's own full-screen request does).
        Method("setFull", "(Z)V", regs=3, code=[
            ("iget-boolean", "v0", "v1", F_FULL),
            ("if-ne", "v0", "v2", "change"),
            ("return-void",),                         # already so: nothing to do
            L("change"),
            ("iput-boolean", "v2", "v1", F_FULL),
            ("invoke-virtual", ["v1"], f"{MA}->applyFull()V"),
            ("return-void",)]),
        Method("applyFull", "()V", regs=5, code=[
            ("invoke-virtual", ["v4"], f"{ACT}->getWindow()Landroid/view/Window;"),
            ("move-result-object", "v1"),
            ("invoke-virtual", ["v1"], "Landroid/view/Window;->getDecorView()Landroid/view/View;"),
            ("move-result-object", "v2"),
            # the picture may use the strip beside the camera (Android 9+), so a video sideways has no black band
            ("sget", "v0", "Landroid/os/Build$VERSION;->SDK_INT:I"),
            ("const/16", "v3", 28),
            ("if-lt", "v0", "v3", "nocut"),
            ("iget-boolean", "v0", "v4", F_FULL),
            ("if-eqz", "v0", "cutoff"),
            ("sget", "v0", "Landroid/os/Build$VERSION;->SDK_INT:I"),
            ("const/16", "v3", 30),
            ("if-lt", "v0", "v3", "cutshort"),
            ("const/4", "v0", 3),                     # ALWAYS (Android 11+): every edge, camera strip included
            ("goto", "cutset"),
            L("cutshort"),
            ("const/4", "v0", 1),                     # SHORT_EDGES (Android 9 and 10)
            ("goto", "cutset"),
            L("cutoff"),
            ("const/4", "v0", 0),                     # DEFAULT: back to the usual safe area
            L("cutset"),
            ("invoke-virtual", ["v1"], f"Landroid/view/Window;->getAttributes(){LP}"),
            ("move-result-object", "v3"),
            ("iput", "v0", "v3", f"{LP}->layoutInDisplayCutoutMode:I"),
            ("invoke-virtual", ["v1", "v3"], f"Landroid/view/Window;->setAttributes({LP})V"),
            L("nocut"),
            ("iget-boolean", "v0", "v4", F_FULL),
            ("if-eqz", "v0", "off"),
            ("const/16", "v0", 0x1706),               # immersive sticky: hide status and navigation bars
            ("invoke-virtual", ["v2", "v0"], "Landroid/view/View;->setSystemUiVisibility(I)V"),
            ("const/4", "v0", 6),                     # SCREEN_ORIENTATION_SENSOR_LANDSCAPE
            ("invoke-virtual", ["v4", "v0"], f"{ACT}->setRequestedOrientation(I)V"),
            ("sget", "v0", "Landroid/os/Build$VERSION;->SDK_INT:I"),
            ("const/16", "v3", 30),
            ("if-lt", "v0", "v3", "done"),            # Android 11+: the current way too (bars come back with a swipe)
            ("invoke-virtual", ["v1"], "Landroid/view/Window;->getInsetsController()Landroid/view/WindowInsetsController;"),
            ("move-result-object", "v1"),
            ("invoke-static", [], "Landroid/view/WindowInsets$Type;->systemBars()I"),
            ("move-result", "v0"),
            ("invoke-interface", ["v1", "v0"], "Landroid/view/WindowInsetsController;->hide(I)V"),
            ("const/4", "v0", 2),                     # BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            ("invoke-interface", ["v1", "v0"], "Landroid/view/WindowInsetsController;->setSystemBarsBehavior(I)V"),
            L("done"),
            ("return-void",),
            L("off"),
            ("const/4", "v0", 0),
            ("invoke-virtual", ["v2", "v0"], "Landroid/view/View;->setSystemUiVisibility(I)V"),
            ("const/4", "v0", -1),                    # SCREEN_ORIENTATION_UNSPECIFIED
            ("invoke-virtual", ["v4", "v0"], f"{ACT}->setRequestedOrientation(I)V"),
            ("sget", "v0", "Landroid/os/Build$VERSION;->SDK_INT:I"),
            ("const/16", "v3", 30),
            ("if-lt", "v0", "v3", "end"),
            ("invoke-virtual", ["v1"], "Landroid/view/Window;->getInsetsController()Landroid/view/WindowInsetsController;"),
            ("move-result-object", "v1"),
            ("invoke-static", [], "Landroid/view/WindowInsets$Type;->systemBars()I"),
            ("move-result", "v0"),
            ("invoke-interface", ["v1", "v0"], "Landroid/view/WindowInsetsController;->show(I)V"),
            L("end"),
            ("return-void",)]),

        # the picture-in-picture window was closed: the video stops (like any video app)
        Method("onStop", "()V", regs=4, code=[
            ("invoke-super", ["v3"], f"{ACT}->onStop()V"),
            ("iget-boolean", "v0", "v3", F_PIP),
            ("if-eqz", "v0", "end"),
            ("const/4", "v0", 0),
            ("iput-boolean", "v0", "v3", F_PIP),
            ("iget-object", "v1", "v3", F_WEB),
            ("const-string", "v2", "window.lentaPipClosed && window.lentaPipClosed()"),
            ("const/4", "v0", 0),
            ("invoke-virtual", ["v1", "v2", "v0"], f"{WV}->evaluateJavascript({STR}{VCB})V"),
            L("end"),
            ("return-void",)]),

        # closing the app: let go of the wake lock
        Method("onDestroy", "()V", regs=2, code=[
            ("const/4", "v0", 0),
            ("invoke-virtual", ["v1", "v0"], f"{MA}->audio(Z)V"),
            ("invoke-super", ["v1"], f"{ACT}->onDestroy()V"),
            ("return-void",)]),

        # leaving the app: write cookies to disk now, so the sign-in survives the app being closed
        Method("onPause", "()V", regs=2, code=[
            ("invoke-super", ["v1"], f"{ACT}->onPause()V"),
            ("invoke-static", [], "Landroid/webkit/CookieManager;->getInstance()Landroid/webkit/CookieManager;"),
            ("move-result-object", "v0"),
            ("invoke-virtual", ["v0"], "Landroid/webkit/CookieManager;->flush()V"),
            ("return-void",)]),

        # Back: leave full screen, else go back in LENTA, else leave the app
        Method("onBackPressed", "()V", regs=2, code=[
            ("iget-object", "v0", "v1", F_CUSTOM),
            ("if-eqz", "v0", "nocustom"),
            ("invoke-virtual", ["v1"], f"{MA}->hideCustom()V"),
            ("return-void",),
            L("nocustom"),
            ("iget-object", "v0", "v1", F_WEB),
            ("invoke-virtual", ["v0"], f"{WV}->canGoBack()Z"),
            ("move-result", "v0"),
            ("if-eqz", "v0", "leave"),
            ("iget-object", "v0", "v1", F_WEB),
            ("invoke-virtual", ["v0"], f"{WV}->goBack()V"),
            ("return-void",),
            L("leave"),
            ("invoke-super", ["v1"], f"{ACT}->onBackPressed()V"),
            ("return-void",)]),

        # full-screen video: the web page's full-screen view on top of everything, sideways, bars hidden
        Method("showCustom", f"(Landroid/view/View;{CVC})V", regs=6, code=[
            ("iget-object", "v0", "v3", F_CUSTOM),
            ("if-eqz", "v0", "ok"),
            ("invoke-interface", ["v5"], f"{CVC}->onCustomViewHidden()V"),
            ("return-void",),
            L("ok"),
            ("iput-object", "v4", "v3", F_CUSTOM),
            ("iput-object", "v5", "v3", F_CB),
            ("invoke-virtual", ["v3"], f"{ACT}->getWindow()Landroid/view/Window;"),
            ("move-result-object", "v0"),
            ("invoke-virtual", ["v0"], "Landroid/view/Window;->getDecorView()Landroid/view/View;"),
            ("move-result-object", "v0"),
            ("check-cast", "v0", "Landroid/view/ViewGroup;"),
            ("new-instance", "v1", "Landroid/widget/FrameLayout$LayoutParams;"),
            ("const/4", "v2", -1),                    # MATCH_PARENT
            ("invoke-direct", ["v1", "v2", "v2"], "Landroid/widget/FrameLayout$LayoutParams;-><init>(II)V"),
            ("invoke-virtual", ["v0", "v4", "v1"], "Landroid/view/ViewGroup;->addView(Landroid/view/View;Landroid/view/ViewGroup$LayoutParams;)V"),
            ("const/16", "v1", 0x1706),               # immersive sticky: hide status and navigation bars
            ("invoke-virtual", ["v4", "v1"], "Landroid/view/View;->setSystemUiVisibility(I)V"),
            ("const/4", "v1", 6),                     # SCREEN_ORIENTATION_SENSOR_LANDSCAPE
            ("invoke-virtual", ["v3", "v1"], f"{ACT}->setRequestedOrientation(I)V"),
            ("return-void",)]),

        Method("hideCustom", "()V", regs=4, code=[
            ("iget-object", "v0", "v3", F_CUSTOM),
            ("if-nez", "v0", "go"),
            ("return-void",),
            L("go"),
            ("invoke-virtual", ["v3"], f"{ACT}->getWindow()Landroid/view/Window;"),
            ("move-result-object", "v1"),
            ("invoke-virtual", ["v1"], "Landroid/view/Window;->getDecorView()Landroid/view/View;"),
            ("move-result-object", "v1"),
            ("check-cast", "v1", "Landroid/view/ViewGroup;"),
            ("invoke-virtual", ["v1", "v0"], "Landroid/view/ViewGroup;->removeView(Landroid/view/View;)V"),
            ("const/4", "v0", 0),
            ("iput-object", "v0", "v3", F_CUSTOM),
            # Put back whatever the app itself was doing, rather than assuming it was doing nothing.
            # This used to set the orientation straight to UNSPECIFIED, which is wrong whenever a page
            # went full screen on top of LENTA's own full-screen player: the sideways lock and the
            # hidden bars went with it, and setFull could not bring them back because as far as it was
            # concerned nothing had changed. applyFull asks the flag what the state should be.
            ("invoke-virtual", ["v3"], f"{MA}->applyFull()V"),
            ("iget-object", "v1", "v3", F_CB),
            ("iput-object", "v0", "v3", F_CB),
            ("if-eqz", "v1", "end"),
            ("invoke-interface", ["v1"], f"{CVC}->onCustomViewHidden()V"),
            L("end"),
            ("return-void",)]),

        # the file picker's answer (artwork uploads in Edit metadata)
        Method("onActivityResult", "(IILandroid/content/Intent;)V", regs=6, code=[
            ("const/4", "v0", FILE_REQUEST),
            ("if-ne", "v3", "v0", "end"),
            ("iget-object", "v0", "v2", F_FILE),
            ("if-eqz", "v0", "end"),
            ("invoke-static", ["v4", "v5"], f"{FCP}->parseResult(ILandroid/content/Intent;)[Landroid/net/Uri;"),
            ("move-result-object", "v1"),
            ("invoke-interface", ["v0", "v1"], f"{VCB}->onReceiveValue({OBJ})V"),
            ("const/4", "v0", 0),
            ("iput-object", "v0", "v2", F_FILE),
            L("end"),
            ("return-void",)]),

        # "Download original file": Android's Download manager, signed in with LENTA's cookie
        Method("onDownloadStart", f"({STR}{STR}{STR}{STR}J)V", regs=12, code=[
            ("const-string", "v0", "http"),           # the Download manager only takes http(s) links
            ("invoke-virtual", ["v6", "v0"], f"{STR}->startsWith({STR})Z"),
            ("move-result", "v0"),
            ("if-nez", "v0", "web"),
            ("return-void",),
            L("web"),
            ("new-instance", "v0", DMR),
            ("invoke-static", ["v6"], f"Landroid/net/Uri;->parse({STR})Landroid/net/Uri;"),
            ("move-result-object", "v1"),
            ("invoke-direct", ["v0", "v1"], f"{DMR}-><init>(Landroid/net/Uri;)V"),
            ("invoke-static", [], "Landroid/webkit/CookieManager;->getInstance()Landroid/webkit/CookieManager;"),
            ("move-result-object", "v1"),
            ("invoke-virtual", ["v1", "v6"], f"Landroid/webkit/CookieManager;->getCookie({STR}){STR}"),
            ("move-result-object", "v1"),
            ("if-eqz", "v1", "nocookie"),
            ("const-string", "v2", "Cookie"),
            ("invoke-virtual", ["v0", "v2", "v1"], f"{DMR}->addRequestHeader({STR}{STR}){DMR}"),
            L("nocookie"),
            ("const-string", "v2", "User-Agent"),
            ("invoke-virtual", ["v0", "v2", "v7"], f"{DMR}->addRequestHeader({STR}{STR}){DMR}"),
            ("invoke-static", ["v6", "v8", "v9"], f"Landroid/webkit/URLUtil;->guessFileName({STR}{STR}{STR}){STR}"),
            ("move-result-object", "v1"),
            ("invoke-virtual", ["v0", "v1"], f"{DMR}->setTitle(Ljava/lang/CharSequence;){DMR}"),
            ("const/4", "v2", 1),                     # VISIBILITY_VISIBLE_NOTIFY_COMPLETED
            ("invoke-virtual", ["v0", "v2"], f"{DMR}->setNotificationVisibility(I){DMR}"),
            ("const-string", "v2", "Download"),       # Environment.DIRECTORY_DOWNLOADS
            ("sget", "v3", "Landroid/os/Build$VERSION;->SDK_INT:I"),
            ("const/16", "v4", 29),
            ("if-lt", "v3", "v4", "old"),
            # Android 10+: the shared Downloads folder, no permission needed
            ("invoke-virtual", ["v0", "v2", "v1"], f"{DMR}->setDestinationInExternalPublicDir({STR}{STR}){DMR}"),
            ("const-string", "v4", "Downloading — you'll find it in Downloads"),
            ("goto", "queue"),
            L("old"),
            # Android 8-9 would need storage permission for it: the app's own Download folder instead
            ("invoke-virtual", ["v0", "v5", "v2", "v1"], f"{DMR}->setDestinationInExternalFilesDir(Landroid/content/Context;{STR}{STR}){DMR}"),
            # and it is not in Downloads, so it must not say it is: on these two versions the file
            # lands in the app's own folder, which is where Files shows it under Android/data.
            ("const-string", "v4", "Downloading — it will be in LENTA's own folder"),
            L("queue"),
            ("const-string", "v2", "download"),       # Context.DOWNLOAD_SERVICE
            ("invoke-virtual", ["v5", "v2"], f"{ACT}->getSystemService({STR}){OBJ}"),
            ("move-result-object", "v2"),
            ("check-cast", "v2", "Landroid/app/DownloadManager;"),
            ("invoke-virtual", ["v2", "v0"], f"Landroid/app/DownloadManager;->enqueue({DMR})J"),
            ("const/4", "v3", 0),
            ("invoke-static", ["v5", "v4", "v3"], "Landroid/widget/Toast;->makeText(Landroid/content/Context;Ljava/lang/CharSequence;I)Landroid/widget/Toast;"),
            ("move-result-object", "v2"),
            ("invoke-virtual", ["v2"], "Landroid/widget/Toast;->show()V"),
            ("return-void",)]),
    ])

    client = ClassDef(CL, "Landroid/webkit/WebViewClient;", fields=[Field("a", MA)], methods=[
        ctor_with_activity(CL, "Landroid/webkit/WebViewClient;"),
        # the page's address changed (LENTA moves between pages by the part after "#"): a video player page
        # (#/play/…) takes the whole screen, sideways; any other page gives it back. Works whatever the page's code.
        Method("doUpdateVisitedHistory", f"({WV}{STR}Z)V", regs=6, code=[
            ("iget-object", "v0", "v2", f"{CL}->a:{MA}"),
            ("const-string", "v1", "#/play/"),
            ("invoke-virtual", ["v4", "v1"], f"{STR}->contains(Ljava/lang/CharSequence;)Z"),
            ("move-result", "v1"),
            ("invoke-virtual", ["v0", "v1"], f"{MA}->setFull(Z)V"),
            ("return-void",)]),
        # LENTA pages stay in the app; other sites open in the browser
        Method("shouldOverrideUrlLoading", f"({WV}{REQ})Z", regs=6, code=[
            ("invoke-interface", ["v5"], f"{REQ}->isForMainFrame()Z"),
            ("move-result", "v0"),
            ("if-eqz", "v0", "inapp"),
            ("invoke-interface", ["v5"], f"{REQ}->getUrl()Landroid/net/Uri;"),
            ("move-result-object", "v0"),
            ("invoke-virtual", ["v0"], f"Landroid/net/Uri;->getScheme(){STR}"),
            ("move-result-object", "v1"),
            # A scheme is case-insensitive by the standard and Uri.getScheme returns it as written,
            # so these are compared that way: "HTTPS://..." used to match none of them and the link
            # was dropped without a word.
            ("const-string", "v2", "file"),
            ("invoke-virtual", ["v2", "v1"], f"{STR}->equalsIgnoreCase({STR})Z"),
            ("move-result", "v2"),
            ("if-nez", "v2", "inapp"),
            ("const-string", "v2", "http"),           # only web links leave the app; others are ignored
            ("invoke-virtual", ["v2", "v1"], f"{STR}->equalsIgnoreCase({STR})Z"),
            ("move-result", "v2"),
            ("if-nez", "v2", "web"),
            ("const-string", "v2", "https"),
            ("invoke-virtual", ["v2", "v1"], f"{STR}->equalsIgnoreCase({STR})Z"),
            ("move-result", "v2"),
            ("if-nez", "v2", "web"),
            ("const/4", "v0", 1),
            ("return", "v0"),
            L("web"),
            ("invoke-virtual", ["v0"], f"Landroid/net/Uri;->getHost(){STR}"),
            ("move-result-object", "v1"),
            ("iget-object", "v2", "v3", f"{CL}->a:{MA}"),
            ("iget-object", "v2", "v2", F_HOST),
            ("if-eqz", "v2", "outside"),
            ("invoke-virtual", ["v2", "v1"], f"{STR}->equalsIgnoreCase({STR})Z"),
            ("move-result", "v1"),
            ("if-nez", "v1", "inapp"),
            L("outside"),
            ("new-instance", "v1", "Landroid/content/Intent;"),
            ("const-string", "v2", "android.intent.action.VIEW"),
            ("invoke-direct", ["v1", "v2", "v0"], f"Landroid/content/Intent;-><init>({STR}Landroid/net/Uri;)V"),
            ("iget-object", "v2", "v3", f"{CL}->a:{MA}"),
            ("invoke-virtual", ["v2", "v1"], f"{ACT}->startActivity(Landroid/content/Intent;)V"),
            ("const/4", "v0", 1),
            ("return", "v0"),
            L("inapp"),
            ("const/4", "v0", 0),
            ("return", "v0")]),
        # remember whether the app's own setup page is showing (only it may set the server)
        Method("onPageStarted", f"({WV}{STR}Landroid/graphics/Bitmap;)V", regs=6, code=[
            ("const-string", "v0", "file:///android_asset/"),
            ("invoke-virtual", ["v4", "v0"], f"{STR}->startsWith({STR})Z"),
            ("move-result", "v0"),
            ("iget-object", "v1", "v2", f"{CL}->a:{MA}"),
            ("iput-boolean", "v0", "v1", F_SETUP),
            ("return-void",)]),
        # HTTPS: LENTA's own certificate ("LENTA local CA") is fine for the saved server (and while connecting on
        # the setup page); any other certificate problem is refused and explained on the setup page
        Method("onReceivedSslError", f"({WV}{SSLH}{SSLE})V", regs=8, code=[
            ("iget-object", "v1", "v4", f"{CL}->a:{MA}"),
            ("invoke-virtual", ["v7"], f"{SSLE}->getCertificate(){SSLC}"),
            ("move-result-object", "v0"),
            ("if-eqz", "v0", "no"),
            ("invoke-virtual", ["v0"], f"{SSLC}->getIssuedBy(){DN}"),
            ("move-result-object", "v0"),
            ("if-eqz", "v0", "no"),
            ("invoke-virtual", ["v0"], f"{DN}->getCName(){STR}"),
            ("move-result-object", "v0"),
            ("if-eqz", "v0", "no"),
            ("const-string", "v2", "LENTA local CA"),
            ("invoke-virtual", ["v0", "v2"], f"{STR}->startsWith({STR})Z"),
            ("move-result", "v0"),
            ("if-eqz", "v0", "no"),
            ("iget-boolean", "v0", "v1", F_SETUP),
            ("if-nez", "v0", "ok"),
            ("iget-object", "v2", "v1", F_HOST),
            ("if-eqz", "v2", "no"),
            ("invoke-virtual", ["v7"], f"{SSLE}->getUrl(){STR}"),
            ("move-result-object", "v0"),
            ("if-eqz", "v0", "no"),
            ("invoke-static", ["v0"], f"Landroid/net/Uri;->parse({STR})Landroid/net/Uri;"),
            ("move-result-object", "v0"),
            ("invoke-virtual", ["v0"], f"Landroid/net/Uri;->getHost(){STR}"),
            ("move-result-object", "v0"),
            ("if-eqz", "v0", "no"),
            ("invoke-virtual", ["v2", "v0"], f"{STR}->equalsIgnoreCase({STR})Z"),
            ("move-result", "v0"),
            ("if-eqz", "v0", "no"),
            L("ok"),
            ("invoke-virtual", ["v6"], f"{SSLH}->proceed()V"),
            ("return-void",),
            L("no"),
            ("invoke-virtual", ["v6"], f"{SSLH}->cancel()V"),
            ("iget-boolean", "v0", "v1", F_SETUP),
            ("if-nez", "v0", "end"),
            ("const-string", "v2", SETUP + "?error=1&ssl=1"),
            ("invoke-virtual", ["v5", "v2"], f"{WV}->loadUrl({STR})V"),
            L("end"),
            ("return-void",)]),
        # a LENTA page finished loading on the saved machine but at another address (LENTA moved to another
        # port, or to https): remember the new address, so the app opens it next time
        Method("onPageFinished", f"({WV}{STR})V", regs=8, code=[
            ("if-eqz", "v7", "end"),
            ("const-string", "v0", "http"),
            ("invoke-virtual", ["v7", "v0"], f"{STR}->startsWith({STR})Z"),
            ("move-result", "v0"),
            ("if-eqz", "v0", "end"),
            ("iget-object", "v1", "v5", f"{CL}->a:{MA}"),
            ("iget-object", "v2", "v1", F_HOST),
            ("if-eqz", "v2", "end"),
            ("invoke-static", ["v7"], f"Landroid/net/Uri;->parse({STR})Landroid/net/Uri;"),
            ("move-result-object", "v3"),
            ("invoke-virtual", ["v3"], f"Landroid/net/Uri;->getHost(){STR}"),
            ("move-result-object", "v0"),
            ("if-eqz", "v0", "end"),
            ("invoke-virtual", ["v2", "v0"], f"{STR}->equalsIgnoreCase({STR})Z"),
            ("move-result", "v0"),
            ("if-eqz", "v0", "end"),
            ("invoke-virtual", ["v3"], f"Landroid/net/Uri;->getScheme(){STR}"),
            ("move-result-object", "v0"),
            ("if-eqz", "v0", "end"),
            ("const-string", "v4", "://"),
            ("invoke-virtual", ["v0", "v4"], f"{STR}->concat({STR}){STR}"),
            ("move-result-object", "v0"),
            ("invoke-virtual", ["v3"], f"Landroid/net/Uri;->getEncodedAuthority(){STR}"),
            ("move-result-object", "v4"),
            ("if-eqz", "v4", "end"),
            ("invoke-virtual", ["v0", "v4"], f"{STR}->concat({STR}){STR}"),
            ("move-result-object", "v0"),
            *prefs("v2", "v1", "v4"),
            ("const-string", "v3", "server"),
            ("const-string", "v4", ""),
            ("invoke-interface", ["v2", "v3", "v4"], f"{SP}->getString({STR}{STR}){STR}"),
            ("move-result-object", "v4"),
            ("invoke-virtual", ["v0", "v4"], f"{STR}->equals({OBJ})Z"),
            ("move-result", "v4"),
            ("if-nez", "v4", "end"),
            ("invoke-interface", ["v2"], f"{SP}->edit(){SPE}"),
            ("move-result-object", "v2"),
            ("invoke-interface", ["v2", "v3", "v0"], f"{SPE}->putString({STR}{STR}){SPE}"),
            ("move-result-object", "v2"),
            ("invoke-interface", ["v2"], f"{SPE}->apply()V"),
            L("end"),
            ("return-void",)]),
        # The WebView's own process died — out of memory on a long film, usually. Android's rule is
        # that if nobody answers this, it kills the whole app: "LENTA keeps stopping", no explanation,
        # nothing in the app able to say what happened. Answering it means the app lives, and since
        # the dead WebView can never be used again the activity is built afresh, which makes a new one
        # and reopens the server. The film's place is lost; the app is not.
        #
        # No guard against doing this repeatedly, deliberately: a renderer that dies every time is a
        # crash loop either way, and today that loop is the app disappearing instead of reappearing.
        Method("onRenderProcessGone", f"({WV}Landroid/webkit/RenderProcessGoneDetail;)Z", regs=4, code=[
            ("iget-object", "v0", "v1", f"{CL}->a:{MA}"),
            ("invoke-virtual", ["v0"], f"{ACT}->recreate()V"),
            ("const/4", "v0", 1),                     # handled: do not take the app down with it
            ("return", "v0")]),
        Method("onReceivedError", f"({WV}{REQ}Landroid/webkit/WebResourceError;)V", regs=6, code=[
            ("invoke-interface", ["v4"], f"{REQ}->isForMainFrame()Z"),
            ("move-result", "v0"),
            ("if-eqz", "v0", "end"),
            ("const-string", "v1", SETUP + "?error=1"),
            ("invoke-virtual", ["v3", "v1"], f"{WV}->loadUrl({STR})V"),
            L("end"),
            ("return-void",)]),
    ])

    chrome = ClassDef(CH, "Landroid/webkit/WebChromeClient;", fields=[Field("a", MA)], methods=[
        ctor_with_activity(CH, "Landroid/webkit/WebChromeClient;"),
        Method("onShowCustomView", f"(Landroid/view/View;{CVC})V", regs=4, code=[
            ("iget-object", "v0", "v1", f"{CH}->a:{MA}"),
            ("invoke-virtual", ["v0", "v2", "v3"], f"{MA}->showCustom(Landroid/view/View;{CVC})V"),
            ("return-void",)]),
        # no grey "play" picture on a video before it starts (the WebView's own default)
        Method("getDefaultVideoPoster", "()Landroid/graphics/Bitmap;", regs=3, code=[
            ("const/4", "v0", 1),
            ("const-string", "v1", "ARGB_8888"),
            ("invoke-static", ["v1"], "Landroid/graphics/Bitmap$Config;->valueOf(Ljava/lang/String;)Landroid/graphics/Bitmap$Config;"),
            ("move-result-object", "v1"),
            ("invoke-static", ["v0", "v0", "v1"], "Landroid/graphics/Bitmap;->createBitmap(IILandroid/graphics/Bitmap$Config;)Landroid/graphics/Bitmap;"),
            ("move-result-object", "v0"),
            ("return-object", "v0")]),
        Method("onHideCustomView", "()V", regs=2, code=[
            ("iget-object", "v0", "v1", f"{CH}->a:{MA}"),
            ("invoke-virtual", ["v0"], f"{MA}->hideCustom()V"),
            ("return-void",)]),
        Method("onShowFileChooser", f"({WV}{VCB}{FCP})Z", regs=7, code=[
            ("iget-object", "v0", "v3", f"{CH}->a:{MA}"),
            ("iget-object", "v1", "v0", F_FILE),
            ("if-eqz", "v1", "fresh"),
            ("const/4", "v2", 0),
            ("invoke-interface", ["v1", "v2"], f"{VCB}->onReceiveValue({OBJ})V"),
            L("fresh"),
            ("iput-object", "v5", "v0", F_FILE),
            ("invoke-virtual", ["v6"], f"{FCP}->createIntent()Landroid/content/Intent;"),
            ("move-result-object", "v1"),
            ("const/4", "v2", FILE_REQUEST),
            ("invoke-virtual", ["v0", "v1", "v2"], f"{ACT}->startActivityForResult(Landroid/content/Intent;I)V"),
            ("const/4", "v0", 1),
            ("return", "v0")]),
    ])

    def ui_call(what, flag=None, num=None):
        """a.runOnUiThread(new Ui(a, what, flag, num)); `this` is v4, the argument (if any) v5"""
        return [("iget-object", "v0", "v4", f"{BR}->a:{MA}"),
                ("new-instance", "v1", UI),
                ("const/4", "v2", what),
                ("const/4", "v3", 0),
                ("invoke-direct", ["v1", "v0", "v2", flag or "v3", num or "v3"], UI_INIT),
                ("invoke-virtual", ["v0", "v1"], f"{ACT}->runOnUiThread(Ljava/lang/Runnable;)V"),
                ("return-void",)]

    bridge = ClassDef(BR, OBJ, fields=[Field("a", MA)], methods=[
        ctor_with_activity(BR, OBJ),
        Method("setServer", f"({STR})V", regs=5, annotations=[JSI], code=[
            ("iget-object", "v0", "v3", f"{BR}->a:{MA}"),
            ("iget-boolean", "v1", "v0", F_SETUP),
            ("if-nez", "v1", "ok"),
            ("return-void",),
            L("ok"),
            *prefs("v1", "v0", "v2"),
            ("invoke-interface", ["v1"], f"{SP}->edit(){SPE}"),
            ("move-result-object", "v1"),
            ("const-string", "v2", "server"),
            ("invoke-interface", ["v1", "v2", "v4"], f"{SPE}->putString({STR}{STR}){SPE}"),
            ("move-result-object", "v1"),
            ("invoke-interface", ["v1"], f"{SPE}->commit()Z"),
            ("new-instance", "v1", UI),
            ("const/4", "v2", 0),
            ("invoke-direct", ["v1", "v0", "v2", "v2", "v2"], UI_INIT),
            ("invoke-virtual", ["v0", "v1"], f"{ACT}->runOnUiThread(Ljava/lang/Runnable;)V"),
            ("return-void",)]),
        Method("server", f"(){STR}", regs=4, annotations=[JSI], code=[
            ("iget-object", "v1", "v3", f"{BR}->a:{MA}"),
            *prefs("v0", "v1", "v2"),
            ("const-string", "v1", "server"),
            ("const-string", "v2", ""),
            ("invoke-interface", ["v0", "v1", "v2"], f"{SP}->getString({STR}{STR}){STR}"),
            ("move-result-object", "v0"),
            ("return-object", "v0")]),
        Method("changeServer", "()V", regs=5, annotations=[JSI], code=ui_call(2)),
        Method("keepScreenOn", "(Z)V", regs=6, annotations=[JSI], code=ui_call(1, flag="v5")),
        Method("audioPlaying", "(Z)V", regs=6, annotations=[JSI], code=ui_call(3, flag="v5")),
        Method("setColors", "(I)V", regs=6, annotations=[JSI], code=ui_call(4, num="v5")),
        Method("fullscreen", "(Z)V", regs=6, annotations=[JSI], code=ui_call(5, flag="v5")),
        Method("version", f"(){STR}", regs=2, annotations=[JSI], code=[
            ("const-string", "v0", VERSION), ("return-object", "v0")]),
    ])

    ui = ClassDef(UI, OBJ, interfaces=["Ljava/lang/Runnable;"],
                  fields=[Field("a", MA), Field("what", "I"), Field("flag", "Z"), Field("n", "I")], methods=[
        Method("<init>", f"({MA}IZI)V", regs=5, code=[
            ("invoke-direct", ["v0"], f"{OBJ}-><init>()V"),
            ("iput-object", "v1", "v0", f"{UI}->a:{MA}"),
            ("iput", "v2", "v0", f"{UI}->what:I"),
            ("iput-boolean", "v3", "v0", f"{UI}->flag:Z"),
            ("iput", "v4", "v0", f"{UI}->n:I"),
            ("return-void",)]),
        Method("run", "()V", regs=4, code=[
            ("iget-object", "v0", "v3", f"{UI}->a:{MA}"),
            ("iget", "v1", "v3", f"{UI}->what:I"),
            ("if-nez", "v1", "not0"),
            ("invoke-virtual", ["v0"], f"{MA}->start()V"),
            ("return-void",),
            L("not0"),
            ("const/4", "v2", 1),
            ("if-ne", "v1", "v2", "not1"),
            ("iget-boolean", "v1", "v3", f"{UI}->flag:Z"),
            ("invoke-virtual", ["v0", "v1"], f"{MA}->keepOn(Z)V"),
            ("return-void",),
            L("not1"),
            ("const/4", "v2", 2),
            ("if-ne", "v1", "v2", "not2"),
            ("invoke-virtual", ["v0"], f"{MA}->openSetup()V"),
            ("return-void",),
            L("not2"),
            ("const/4", "v2", 3),
            ("if-ne", "v1", "v2", "not3"),
            ("iget-boolean", "v1", "v3", f"{UI}->flag:Z"),
            ("invoke-virtual", ["v0", "v1"], f"{MA}->audio(Z)V"),
            ("return-void",),
            L("not3"),
            ("const/4", "v2", 5),
            ("if-ne", "v1", "v2", "not5"),
            ("iget-boolean", "v1", "v3", f"{UI}->flag:Z"),
            ("invoke-virtual", ["v0", "v1"], f"{MA}->setFull(Z)V"),
            ("return-void",),
            L("not5"),
            ("iget", "v1", "v3", f"{UI}->n:I"),
            ("invoke-virtual", ["v0", "v1"], f"{MA}->colors(I)V"),
            ("return-void",)]),
    ])
    return [main, client, chrome, bridge, ui]
