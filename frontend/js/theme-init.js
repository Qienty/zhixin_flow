    /* 在样式生效前定主题，避免刷新时闪一下白底。auto 跟随系统。
    顺手把 theme-color 也定下来：手动选的主题要盖得住系统偏好，
    不然系统深色 + 手动浅色时，状态栏和页面会是两个颜色。 */
    (function () {
      var dark = false;
      try {
        var saved = localStorage.getItem("tether.theme") || "auto";
        dark = saved === "dark" || (saved === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
      } catch (e) {
        dark = false;
      }
      document.documentElement.dataset.theme = dark ? "dark" : "light";
      var bar = document.querySelector('meta[name="theme-color"]');
      if (bar) bar.setAttribute("content", dark ? "#141815" : "#F6F3EC");
    })();
