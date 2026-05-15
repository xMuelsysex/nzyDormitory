我需要你制作一个网页，网页需要从 https://webvpn.njucm.edu.cn/http/webvpn34f6d2940beaaa8a549e2c772ae7c064/web/auths/index.aspx 里的自助购电项目里面提取当前的宿舍信息，这个活动需要定时执行，定时，开始和结束时间都由用户来设置。
注意，在使用前得先让用户通过 https://webvpn.njucm.edu.cn/http/webvpn34f6d2940beaaa8a549e2c772ae7c064/web/auths/index.aspx 进行登录操作，登录完成之后让用户选择所属楼橦和您的房间。
这个网页将运行在ubuntu服务器上，请注意
定时设置之后，要根据每段时间读取到的电费进行画图，以便于让用户更加清楚的了解到电量消耗的趋势
当电费低于一定阈值的时候（阈值由用户自己设定），向指定的邮箱地址发送邮件进行提醒，邮箱地址同样由用户指定
