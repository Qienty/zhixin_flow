    /* 装到主屏之后要能离线打开外壳（接口是本机服务，离线照样连不上）。
    注册放在最后，失败也不影响正常使用。 */
    if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
      addEventListener("load", function () {
        navigator.serviceWorker.register("./sw.js").catch(function () {});
      });
    }
