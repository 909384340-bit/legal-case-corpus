#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03_desensitize.py —— 数据治理/脱敏环节（对应课程「数据治理与安全」）

课程给出四种脱敏方案，本脚本组合使用并在代码中标注了对应方案：
  方案一 正则表达式   ：案号、身份证号、手机号、统一社会信用代码、银行账号、
                        邮箱、车牌号、出生日期；
  方案二 词典匹配     ：司法实体词典（法院/检察院/公安/律所/公司/产品/地名）
                        + 泛称停用词典（"公交公司""该公司"等非特定主体不脱敏）；
  方案三 规则 / NER   ：匿名化姓氏模式（黄某欢、罗某、艾某）＋ 频次确认
                        （出现≥2次或出现在标题"X诉Y"结构中才认定为当事人）、
                        机构后缀规则、简称归并（"某信用管理有限公司"↔"某信用公司"）；
  方案四 大模型脱敏   ：脚本输出"拿不准的疑似实体"清单
                        （logs/llm_review_candidates.md），由大模型复核后回填
                        （--apply-llm data/llm_patch.json），形成人机闭环。

脱敏口径（可解释、可审计）：
  · 脱敏：自然人姓名、法人/非法人组织名称、产品与应用名、地方法院/检察院/
          侦查机关名称、律师事务所、案号、行政区划与地址、联系方式、证件号码；
  · 保留：最高人民法院（数据发布与溯源主体、公共权威机构）、
          法律法规及法条名称、通用法律术语、泛指主体（"公交公司""本公司"）；
  · 一致性：全局映射表保证同一实体在所有文件中使用同一占位符；
           映射表本身不进入仓库（.gitignore），避免"脱敏后仍能反查"。
"""
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLEAN_DIR = os.path.join(ROOT, "data", "clean")
FINAL_DIR = os.path.join(ROOT, "data", "final")
LOG_DIR = os.path.join(ROOT, "logs")
os.makedirs(FINAL_DIR, exist_ok=True)

MAP_PATH = os.path.join(ROOT, "data", ".masking_map.json")
CST = timezone(timedelta(hours=8))

# ------------------------------------------------------------------ 保护与白名单
KEEP_ORGS = {"最高人民法院", "最高人民法院审判委员会"}
PROTECT_RE = re.compile(r"《[^》]{2,40}》")                 # 法律法规名称整体保护

# ------------------------------------------------------------------ 方案一：结构化敏感信息
REGEX_RULES = [
    ("证件号码", re.compile(r"\b\d{17}[\dXx]\b"), "[证件号码已删除]"),
    ("联系方式", re.compile(r"\b1[3-9]\d{9}\b"), "[联系方式已删除]"),
    ("统一社会信用代码", re.compile(r"\b[0-9A-HJ-NPQRTUWXY]{18}\b"), "[统一社会信用代码已删除]"),
    ("银行账号", re.compile(r"\b\d{16,19}\b"), "[银行账号已删除]"),
    ("电子邮箱", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[电子邮箱已删除]"),
    ("车牌号", re.compile(r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼]"
                          r"[A-Z][A-Z0-9]{4,6}"), "[车牌号已删除]"),
    ("出生日期", re.compile(r"(?:19|20)\d{2}年\d{1,2}月\d{1,2}日出生"), "[出生日期已删除]"),
    ("案号", re.compile(r"[（(]\s*(?:19|20)\d{2}\s*[)）]\s*[\u4e00-\u9fa5A-Za-z0-9]{1,16}?\s*\d{1,6}\s*号"), None),
]

# ------------------------------------------------------------------ 方案二/三：实体规则
SURNAMES = ("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章"
            "云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝安常乐于"
            "时傅皮齐康伍余元卜顾孟平黄和穆萧尹姚邵汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈"
            "项祝董梁杜阮蓝闵席季麻强贾路娄江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管"
            "卢莫经房裘缪解应宗丁宣邓郁单杭洪包诸左石崔吉钮龚程嵇邢滑裴陆荣翁荀羊惠甄曲家封芮"
            "储靳邴松井段富巫乌焦巴弓牧山谷车侯全班仰秋仲伊宫宁仇栾甘厉戎祖武符刘景詹束龙叶幸"
            "司黎薄印宿白怀蒲从鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻莘党翟贡劳姬申扶堵冉宰郦雍桑"
            "桂濮牛寿边燕冀浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都"
            "耿满弘匡国文寇广禄阙东欧沃利蔚越隆师巩聂晁冷辛阚那简饶曾沙养鞠须丰巢关蒯相查后荆"
            "红游权盖益桓公上官令狐邝郄苟缑亢缑阚")

ENTITY_RULES = [
    ("律师事务所", re.compile(r"[\u4e00-\u9fa5A-Za-z（）()]{2,20}?律师事务所"), "律师事务所"),
    ("侦查机关", re.compile(r"[\u4e00-\u9fa5]{2,12}?(?:公安厅|公安局|公安分局|派出所)"), "侦查机关"),
    ("检察机关", re.compile(r"[\u4e00-\u9fa5]{2,12}?人民检察院"), "检察机关"),
    ("审理法院", re.compile(r"[\u4e00-\u9fa5]{2,12}?(?:人民法院|法院)"), "审理法院"),
    ("法人", re.compile(r"[\u4e00-\u9fa5A-Za-z0-9（）()·]{2,18}?(?:股份有限公司|有限责任公司|"
                        r"有限公司|集团公司|公司|研究院|银行|医院|学校|集团)"), "法人"),
    ("非法人组织", re.compile(r"[\u4e00-\u9fa5]{2,15}?(?:合伙企业|事务所|工作室|协会|基金会)"), "非法人组织"),
    # 只匹配"某应用/某平台"这类匿名产品名，避免把"计算机软件"等通用术语切碎
    ("产品与应用", re.compile(r"某[\u4e00-\u9fa5A-Za-z]{0,3}(?:应用|平台|软件|系统|小程序|APP)(?!有限公司|公司|集团)"), "产品"),
]
ENTITY_RX = {etype: rx for etype, rx, _ in ENTITY_RULES}

# 泛指主体：不是特定法人，不脱敏（词典匹配中的"停用词"）
GENERIC_ORGS = {"公交公司", "该公司", "本公司", "甲公司", "乙公司", "丙公司", "子公司", "总公司",
                "母公司", "关联公司", "两公司", "四公司", "三公司", "各公司", "原告公司", "被告公司",
                "第三方公司", "涉案公司", "上诉公司", "被诉公司", "网络公司", "热力公司",
                "自来水公司", "供电公司", "燃气公司", "客运公司", "公交集团", "集团公司",
                "保险公司", "银行", "医院", "学校", "研究院", "人民法院", "人民检察院"}

# 机构名若含这些词，说明匹配越界（把句子成分吃进来了），丢弃
BAD_ORG_WORDS = ("诉", "案", "纠纷", "判决", "裁定", "请求", "主张", "行为", "服务", "功能",
                 "协议", "用户", "账户", "付款", "乘车", "赔偿", "侵权", "争议", "上诉",
                 "审理", "执行", "认定", "查明", "公司信息", "技术公司信息")

PLACE_RE = re.compile(
    r"[\u4e00-\u9fa5]{2,8}?(?:自治区|特别行政区|省)[\u4e00-\u9fa5]{2,8}?市|"
    r"[\u4e00-\u9fa5]{2,8}?(?:自治区|特别行政区|省)|"
    r"[\u4e00-\u9fa5]{2,8}?(?:市|盟|州|地区)|"
    r"[\u4e00-\u9fa5]{2,8}?(?:区|县|旗|镇|乡|村|街道|路|号|弄|幢|栋|单元|室)|"
    r"[\u4e00-\u9fa5]{2,6}(?:收费站|服务区|高铁站|火车站|汽车站|车站|南站|北站|东站|西站|路口|大桥|码头)"
)
PLACE_STOP = {"人民法院", "高级人民法院", "中级人民法院", "基层人民法院", "最高人民法院",
              "自治区", "特别行政区", "人民检察院", "有限公司", "字号", "城市", "直辖市",
              "高级", "中级", "基层", "专门", "知识", "互联网", "铁路", "海事", "森林",
              "军事", "开发区", "自治区高级", "审判委员会", "地区",
              # 含"市/区/号"但不表示行政区划的通用词，防止过度脱敏
              "手机号", "号码", "编号", "序号", "型号", "账号", "挂号", "口号", "市区",
              "小区", "社区", "校区", "园区", "辖区", "地区", "时区", "区域", "路段",
              "单位", "单数", "分数", "倍数", "次数", "人数", "件数", "套数", "组数"}

# 省级行政区（文中常省略"省/市"后缀，需词典兜底）
PROVINCES = ["河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽", "福建",
             "江西", "山东", "河南", "湖北", "湖南", "广东", "海南", "四川", "贵州",
             "云南", "陕西", "甘肃", "青海", "内蒙古", "广西", "西藏", "宁夏", "新疆",
             "香港", "澳门", "台湾"]

CITIES = ["北京", "上海", "天津", "重庆", "广州", "深圳", "杭州", "南京", "武汉", "成都",
          "西安", "苏州", "青岛", "宁波", "温州", "厦门", "长沙", "郑州", "济南", "合肥",
          "福州", "昆明", "大连", "沈阳", "哈尔滨", "长春", "石家庄", "太原", "南昌",
          "贵阳", "南宁", "兰州", "海口", "无锡", "佛山", "东莞", "泉州", "南通", "常州",
          "烟台", "唐山", "徐州", "绍兴", "嘉兴", "金华", "台州", "湖州", "珠海", "中山",
          "惠州", "潍坊", "洛阳", "襄阳", "株洲", "芜湖", "赣州", "漳州", "汕头", "保定"]

# 名称前缀中的虚词与通用商业角色词，需从实体名头部剥离
# 注意：只收句法虚词与明确的双音节虚词，避免误伤"某信用""某应用"等名称组成部分
FILLER_WORDS = ("通过", "依据", "根据", "关于", "对于", "按照", "参照", "经由",
                "运营商", "服务商", "提供商", "供应商", "销售商", "代理商", "经销商",
                "遂", "在", "由", "向", "与", "和", "对", "为", "是", "被", "将", "把",
                "从", "以", "其", "的", "了", "并", "但", "因", "故", "该", "另", "又",
                "再", "即", "也", "还", "就", "所", "于", "至", "经", "依", "据", "按",
                "及", "等", "之", "而", "则", "如")


# 通用商业角色词：出现在名称前半段时说明匹配越界，需连同其左侧一起剥离
ROLE_WORDS = ("运营商", "服务商", "提供商", "供应商", "销售商", "代理商", "经销商",
              "账户", "用户", "客户", "平台", "公司", "企业", "单位", "中心")

DELIMS = set("，。；：、\"'“”‘’（）()《》 \t\n")
# 向左扩展时的停止字符：仅句法虚词与标点，不含"用/有/中"等可能出现在机构名中的字
STOP_CHARS = set("的了和与在是也被将对从以为及其之于并但而则如使让给等由向把所还就"
                 "又再即故因另至经据按该各每着")


def strip_filler(name):
    """剥离实体名头部的虚词与通用商业角色词（"通过某应用" -> "某应用"）"""
    changed = True
    while changed and len(name) > 2:
        changed = False
        for w in FILLER_WORDS:
            if name.startswith(w) and len(name) - len(w) >= 2:
                name = name[len(w):]
                changed = True
    return name


# 截断词：出现在候选名中说明匹配越界（把句子成分吃进来了），
# 处理方式是"从最后一个截断词之后截取"，而非整条丢弃
TRUNC_WORDS = ("诉", "案", "纠纷", "判决", "裁定", "请求", "主张", "行为", "协议",
               "用户", "账户", "付款", "乘车", "赔偿", "侵权", "争议", "上诉",
               "审理", "执行", "认定", "查明", "关于", "系争", "涉案")


# 截断词：出现在候选名中说明匹配越界（把句子成分吃进来了），
# 处理方式是"从最后一个截断词之后截取"，而非整条丢弃
TRUNC_WORDS = ("诉", "案", "纠纷", "判决", "裁定", "请求", "主张", "行为", "协议",
               "用户", "账户", "付款", "乘车", "赔偿", "侵权", "争议", "上诉",
               "审理", "执行", "认定", "查明", "关于", "系争", "涉案")


def clean_entity_name(name):
    """清理实体名：剥离头部虚词，并迭代剥离越界的商业角色词左侧部分"""
    name = strip_filler(name)
    for w in TRUNC_WORDS:                      # 先做越界截断
        idx = name.rfind(w)
        if idx >= 0 and len(name) - (idx + len(w)) >= 3:
            name = strip_filler(name[idx + len(w):])
    changed = True
    while changed and len(name) > 2:
        changed = False
        for w in ROLE_WORDS:
            idx = name.rfind(w)
            if 0 <= idx <= max(1, len(name) // 2):
                tail = name[idx + len(w):]
                if len(tail) >= 3:
                    name = strip_filler(tail)
                    changed = True
    return name


def valid_org(name):
    name = strip_filler(name)
    if name in GENERIC_ORGS or name in KEEP_ORGS:
        return False
    if any(w in name for w in BAD_ORG_WORDS):
        return False
    if len(name) < 3:
        return False
    return True


def overlap_candidates(text, rx):
    """重叠扫描：零宽断言捕获，避免长匹配吞掉内部实体"""
    pat = re.compile(r"(?=(" + rx.pattern + r"))")
    return {m.group(1) for m in pat.finditer(text)}


def suffix_anchored_candidates(text, suffix_alt, max_len=18):
    """后缀锚定：以机构后缀为锚点向左扩展，避免把句子成分吃进实体名"""
    rx = re.compile(suffix_alt)
    out = set()
    for m in rx.finditer(text):
        start, end = m.start(), m.end()
        i = start
        while i > 0 and start - i < max_len:
            ch = text[i - 1]
            if ch in DELIMS or ch in STOP_CHARS:
                break
            i -= 1
        name = text[i:end]
        if len(name) >= 3:
            out.add(name)
    return out


def extract_persons(corpus, title, min_freq=2):
    """方案三：匿名化姓氏模式 + 频次确认 + 标题结构确认"""
    # 说明：中文无词边界，故不加左侧否定断言（否则"被告人李某"会漏判），
    #      改用"基础短名频次确认"过滤噪声
    rx = re.compile(r"[" + SURNAMES + r"]某[\u4e00-\u9fa5]{0,1}")
    freq = defaultdict(int)
    for m in rx.finditer(corpus):
        freq[m.group(0)] += 1

    title_persons = set()
    if title:
        for m in re.finditer(r"([" + SURNAMES + r"]某[\u4e00-\u9fa5]?)(?:诉|与|、|等)", title):
            title_persons.add(m.group(1))
        for m in re.finditer(r"^(?:被告人|上诉人|原告)?([" + SURNAMES + r"]某[\u4e00-\u9fa5]?)", title):
            title_persons.add(m.group(1))
        title_persons |= {t for t in list(title_persons) if len(t) == 2}

    # 匿名化姓氏模式会带出后随汉字（"黄某欢发现"->"黄某欢发"），
    # 因此用"基础短名（姓氏某）"的频次确认，再取频次最高的完整形态
    freq_base = defaultdict(int)
    for c, n in freq.items():
        freq_base[c[:2]] += n

    confirmed = set()
    for base, cnt in freq_base.items():
        if cnt < min_freq and base not in title_persons:
            continue
        fulls = [c for c in freq if c.startswith(base)]
        best = max(fulls, key=lambda c: (freq[c], len(c)))
        bad_tail = set("的了及为因向与和在是被将对从其并但又再即也就所等"
                       "公司有限集团股份技术科学信息网络服务汽车机械重工种农业"
                       "仪表模具传媒银行院所中心平台软件系统")
        if len(best) == 3 and best[-1] in bad_tail:
            best = base
        confirmed.add(best if freq[best] >= 2 else base)
        confirmed.add(base)          # 基础短名兜底，避免"李某"残留

    # 按长度降序替换即可保证"黄某欢"先于"黄某"命中，无需删除短名
    return sorted(confirmed, key=len, reverse=True)


class Masker:
    """全局一致性脱敏器：同一实体在所有文件中映射到同一占位符"""

    def __init__(self):
        self.mapping = {}
        self.counters = defaultdict(int)

    def _new(self, prefix):
        self.counters[prefix] += 1
        return f"[{prefix}{self.counters[prefix]:02d}]"

    @staticmethod
    def core(name):
        """核心名：去掉行业/组织形态修饰词，用于简称归并"""
        return re.sub(r"(有限责任|股份有限|有限|责任|股份|集团|管理|科技|信息技术|技术发展|"
                      r"贸易|实业|控股|国际|网络|电子|机械|重工|汽车|能源|建设|发展|文化传媒)",
                      "", name)

    def get(self, name, prefix):
        if name in self.mapping:
            return self.mapping[name]
        if len(name) >= 2:
            # ① 包含关系归并："某信用公司" ⊂ "某信用管理有限公司"
            for exist, ph in self.mapping.items():
                if ph.startswith(f"[{prefix}") and len(exist) >= 2 and (name in exist or exist in name):
                    self.mapping[name] = ph
                    return ph
            # ② 核心名归并：去掉"管理/有限"等修饰后相同，视为同一主体
            cn = self.core(name)
            if len(cn) >= 2:
                for exist, ph in self.mapping.items():
                    if ph.startswith(f"[{prefix}") and self.core(exist) == cn:
                        self.mapping[name] = ph
                        return ph
        ph = self._new(prefix)
        self.mapping[name] = ph
        return ph

    # ---------------------------------------------------------- 单文件脱敏
    def process(self, text, title, persons, stats):
        protected = {}

        def _prot_str(s):
            key = f"\u0000P{len(protected)}\u0000"
            protected[key] = s
            return key

        text = PROTECT_RE.sub(lambda m: _prot_str(m.group(0)), text)
        for keep in KEEP_ORGS:          # 白名单机构整体保护，防止被切碎
            if keep in text:
                text = text.replace(keep, _prot_str(keep))

        # ① 结构化敏感信息
        for label, rx, repl in REGEX_RULES:
            if repl:
                text, n = rx.subn(repl, text)
            else:
                text, n = rx.subn(lambda m: self.get(m.group(0), "案号"), text)
            if n:
                stats[label] = stats.get(label, 0) + n

        # ①-2 匿名化机构名预置规则："某信用管理有限公司""某科技有限公司"等
        # 官方匿名化的法人名形如"某信用管理有限公司""西某工业软件有限公司"，
        # 前缀可能是"某"，也可能是"任意单字+某"（姓氏字不固定，故不依赖姓氏表）
        ANON_ORG = (r"(?:[\u4e00-\u9fa5]某|某)[\u4e00-\u9fa5A-Za-z0-9（）()]{0,12}?"
                    r"(?:股份有限公司|有限责任公司|有限公司|集团公司|公司|研究院|银行|集团)")
        for name in sorted({clean_entity_name(c) for c in
                            overlap_candidates(text, re.compile(ANON_ORG))}, key=len, reverse=True):
            if name in GENERIC_ORGS or any(w in name for w in BAD_ORG_WORDS) or len(name) < 3:
                continue
            ph = self.get(name, "法人")
            cnt = text.count(name)
            if cnt:
                text = text.replace(name, ph)
                stats["法人"] = stats.get("法人", 0) + cnt

        # ② 自然人（先于机构处理，避免"黄某欢在某应用"被产品规则切碎）
        #    但若"姓氏某"后紧跟机构后缀（西某工业、沃某模具、青某重工），说明它是
        #    法人名的一部分而非自然人，交由匿名机构名规则整体处理
        ORG_TAIL = ("工业", "科技", "重工", "机械", "汽车", "仪器", "农业", "软件", "模具",
                    "集团", "公司", "有限", "控股", "网络", "信息", "技术", "传媒", "热电",
                    "能源", "化工", "电子", "医药", "食品", "建设", "地产", "物流", "贸易",
                    "实业", "投资", "银行", "电气", "智能", "数据", "环保", "材料", "生物")
        for name in persons:
            if name not in text:
                continue
            ph = self.get(name, "自然人")
            hit = [0]

            def _rep(m, ph=ph, hit=hit):
                tail = m.string[m.end():m.end() + 2]
                if any(tail.startswith(w) for w in ORG_TAIL):
                    return m.group(0)
                hit[0] += 1
                return ph

            text = re.sub(re.escape(name), _rep, text)
            if hit[0]:
                stats["自然人"] = stats.get("自然人", 0) + hit[0]

        # ③ 机构/组织/产品实体：后缀锚定 + 重叠扫描 + 名称清洗，长实体优先
        ORG_SUFFIX = (r"(?:股份有限公司|有限责任公司|有限公司|集团公司|公司|研究院|"
                      r"银行|医院|学校|集团|律师事务所|合伙企业|事务所|工作室|协会|基金会|"
                      r"人民检察院|人民法院|法院|公安厅|公安局|公安分局|派出所)")
        raw_cands = suffix_anchored_candidates(text, ORG_SUFFIX)
        raw_cands |= overlap_candidates(text, ENTITY_RX["产品与应用"])
        for c in raw_cands:
            name = clean_entity_name(c)
            if len(name) < 3:
                continue
            if any(w in name for w in BAD_ORG_WORDS):
                continue
            if name in GENERIC_ORGS or name in KEEP_ORGS:
                continue
            if name.endswith(("人民法院", "法院")):
                if name in ("人民法院",):
                    continue
                prefix = "审理法院"
            elif name.endswith("人民检察院"):
                prefix = "检察机关"
            elif name.endswith(("公安厅", "公安局", "公安分局", "派出所")):
                prefix = "侦查机关"
            elif name.endswith("律师事务所"):
                prefix = "律师事务所"
            elif name.endswith(("应用", "平台", "软件", "系统", "小程序", "APP")):
                prefix = "产品"
            else:
                prefix = "法人"
            ph = self.get(name, prefix)
            # 保留被剥离掉的前缀虚词，避免破坏句子（"通过某应用" -> "通过[产品01]"）
            new_str = ph if c == name else c.replace(name, ph)
            if new_str == c:
                continue
            cnt = text.count(c)
            if cnt:
                text = text.replace(c, new_str)
                stats[prefix] = stats.get(prefix, 0) + cnt

        # ④ 行政区划与地址（可反向识别主体）
        place_cands = {strip_filler(m.group(0)) for m in PLACE_RE.finditer(text)}
        place_cands |= {p for p in PROVINCES + CITIES if p in text}   # 词典兜底
        for name in sorted(place_cands, key=len, reverse=True):
            if name in PLACE_STOP or name in KEEP_ORGS or len(name) < 2:
                continue
            ph = self.get(name, "地点")
            cnt = text.count(name)
            if cnt:
                text = text.replace(name, ph)
                stats["地点"] = stats.get("地点", 0) + cnt

        # ⑤ 还原受保护内容（法律法规名、白名单机构）；协议/书名号内若含地名同样脱敏
        for key, val in protected.items():
            for p in PROVINCES + CITIES:
                if p in val and not any(k in val for k in KEEP_ORGS):
                    val = val.replace(p, self.get(p, "地点"))
            text = text.replace(key, val)
        return text

    def apply_llm_patch(self, patch):
        for name, prefix in patch.items():
            self.get(name, prefix)


def scan_candidates(text):
    """方案四：输出拿不准的疑似实体，交大模型/人工复核"""
    cands = set()
    for m in re.finditer(r"[\u4e00-\u9fa5]{2,6}(?:公司|集团|银行|中心)", text):
        cands.add(m.group(0))
    for m in re.finditer(r"(?:被告人|原告|上诉人|当事人|被执行人)[\u4e00-\u9fa5]{2,3}", text):
        cands.add(m.group(0))
    return sorted(cands)


def main():
    patch_path = None
    if "--apply-llm" in sys.argv:
        patch_path = sys.argv[sys.argv.index("--apply-llm") + 1]

    with open(os.path.join(CLEAN_DIR, "clean_report.json"), encoding="utf-8") as f:
        items = json.load(f)

    # 先通读全量语料，用统计确认当事人姓名（跨文件一致）
    corpus = "\n".join(open(os.path.join(CLEAN_DIR, i["clean_file"]), encoding="utf-8").read()
                       for i in items if os.path.exists(os.path.join(CLEAN_DIR, i["clean_file"])))
    persons = extract_persons(corpus, " ".join(i["title"] for i in items))
    print(f"[info] 频次确认的自然人实体：{len(persons)} 个 -> {persons[:12]}")

    masker = Masker()
    if patch_path and os.path.exists(patch_path):
        with open(patch_path, encoding="utf-8") as f:
            masker.apply_llm_patch(json.load(f))
        print(f"[info] 已载入大模型复核补丁：{patch_path}")

    report, all_candidates = [], set()
    for item in items:
        src = os.path.join(CLEAN_DIR, item["clean_file"])
        with open(src, encoding="utf-8") as f:
            text = f.read()

        all_candidates.update(scan_candidates(text))
        stats = {}
        masked = masker.process(text, item["title"], persons, stats)

        out_name = item["clean_file"].replace(".md", ".desensitized.md")
        with open(os.path.join(FINAL_DIR, out_name), "w", encoding="utf-8") as f:
            f.write(masked)

        report.append({"seq": item["seq"], "title": item["title"], "file": out_name,
                       "mask_counts": stats, "mask_total": sum(stats.values()),
                       "chars": len(masked)})
        print(f"[{item['seq']:02d}] 脱敏完成 {out_name}（{sum(stats.values())} 处）")

    with open(MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(masker.mapping, f, ensure_ascii=False, indent=2)
    with open(os.path.join(LOG_DIR, "desensitize_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    with open(os.path.join(LOG_DIR, "llm_review_candidates.md"), "w", encoding="utf-8") as f:
        f.write("# 大模型复核候选清单（方案四：大模型脱敏）\n\n"
                "> 由 03_desensitize.py 自动生成。复核后把确认的主体写入 "
                "`data/llm_patch.json`（格式 {\"原名\": \"类型\"}），重跑 "
                "`python3 scripts/03_desensitize.py --apply-llm data/llm_patch.json` 即可回填。\n\n")
        for c in all_candidates:
            f.write(f"- {c}\n")

    print(f"=== 脱敏完成：{len(report)} 个文件，共 {sum(r['mask_total'] for r in report)} 处替换，"
          f"映射实体 {len(masker.mapping)} 个 ===")


if __name__ == "__main__":
    main()
