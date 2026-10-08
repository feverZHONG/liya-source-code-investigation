# .NET 二进制的「机制」藏在 IL 里 · 纯 Python 反汇编取权重表

> 触发：手上只有一个 .NET 编译产物（`.dll` / `.exe`；游戏 MOD、桌面插件、客户端加壳件），
> 要弄清它到底怎么跑——**「按权重随机」「几率领」「冷却多久」这类数字不在配置文件里**
> （XML／JSON 只写字段名与部分初值），在 IL 的立即数里。
> 不反汇编就只剩两条路：凭印象编，或者去问作者。两条都不行。

## 难点在哪（先看这句）

**拆 DLL 本身不难**——`dnfile` + `dncil` 两条命令，纯 Python，不用 .NET / mono，也不需要 root。
真正的成本在**先想到它在那儿**：配置文件只给字段与初值，会让人以为「参数都在这儿了」，
于是「按概率抽哪几档」这种问题就变成凭印象编。**判断难，工具不难。**

## 为什么需要它

配置文件侧能拿到的是**声明与初值**（`triggerChance: 0.05` 这种写死的参数可见），
但「触发之后掷哪几档、各占多少权重」是**代码里的立即数**（`ldc.r4 12.5`），配置文件里一个字都没有。

## 前置（一次装好）

```bash
uv venv .venv-dotnet --python 3.13
uv pip install --python .venv-dotnet/bin/python dnfile dncil
```

- **不用装 .NET / mono**：`dnfile` 读 PE + 元数据，`dncil` 解析 IL，纯 Python。
- 这类环境通常没有 `dotnet`、`mono`，也没有 sudo——这条路是**唯一不装 SDK 也能走的**。

## 工具：`scripts/ildump.py`（本仓自带）

```bash
DLL="<解包目录>/Assemblies/Foo.dll"
PY=.venv-dotnet/bin/python

$PY scripts/ildump.py "$DLL" types  "Poison"      # 列类型
$PY scripts/ildump.py "$DLL" list   "Poison|Food" # 列方法（类型.方法）
$PY scripts/ildump.py "$DLL" dis    "JobDriver_PoisonFood\.<MakeNewToils>b__4_0"   # 反汇编单个方法
$PY scripts/ildump.py "$DLL" dis    "." > all_il.txt   # 全量（十几万行量级，够 grep）
```

`dis` 会把 `ldstr` 的字面量、`call` 的方法名、`ldsfld` 的字段名都**解析成可读名字**
（不然只能看到 token 号，没法读）。

## 怎么找（顺序照抄）

1. **先找字符串**：`strings -n 5 Foo.dll | grep -i 关键词`，再用 `ildump.py … strings <正则>` 拿类名／键名。
2. **列方法**：`ildump.py … list <正则>`，用 `|` 一次把相关类全捞出来。
3. **看主逻辑方法**：名字里带 `MakeNewToils` / `CompPostTick` / `HasJobOnThing` / `RunInt` 这类入口的最常见。
4. **编译器的壳要拆**：迭代器方法（如 `MakeNewToils`）的方法体只有一行 `newobj <MakeNewToils>d__4`——
   真正的逻辑在编译器生成的 `<MakeNewToils>d__4.MoveNext` 与**闭包** `<MakeNewToils>b__4_0` 里。
   **看到「方法体只有几行、还带 `<>` 名字」= 去找同名嵌套类型与 `b__` 闭包。**
5. **权重表长什么样**：一长串 `ldftn <方法A> → newobj → ldc.r4 12.5 → newobj → callvirt Add` 重复 N 次，
   接着一个开关调用后**再来一组**，最后 `TryRandomElementByWeight` 收口。
   → 每组的 `ldc.r4` 就是权重、`ldftn` 指的方法就是该档的实现。
6. **档位效果**：再看各档方法体里的 `ldsfld <条目Def>`／`GeneratePawn`／`ReceiveLetter`——效果与条目名就能对上。

## 坑

1. **dnfile 版本差异**（0.18 实测）：`TypeDefRow.MethodList` 是 `MDTableIndex` 列表（`mi.row_index`）；
   `MemberRef` / `TypeRef` 行**没有** `.row_index`（要用 `enumerate` 自己数）；token 的行号属性是 `.rid` 不是 `.row`。
2. **`CilMethodBody` 没有 `from_bytes`**——用 `from dncil.cil.body.reader import read_method_body_from_bytes`。
3. **同名嵌套类型会有多份**（`<X>d__4` 出现两次、分属两个类）——靠 `rva` 与上下文区分，别认错。
4. **权重是相对值不是百分比**——总和随扩展／配置开关变化（实测一组：无扩展 60、有扩展 68，某档从 8.33% 掉到 7.35%）。
   报数时**两个分母都给**，别只报一个。
5. **配置与 DLL 会打架**——实测：配置文件里那个 class 名是复制粘贴残留，DLL 里对应的类**没人引用**
   （实际逻辑走别的路径）。**以 DLL 调用链为准，配置文件只作线索。**
6. **别把「注释掉的配置」当不存在**——条目被注释掉了，逻辑可能改由别处挂上（等量）。两边都要看，结论才准。
7. **频率别口算**——有冷却的随机过程手算容易错，写个 `random` 模拟（实测：6.25 天周期 / 5% / 冷却 1.5 天 → 均值 2.09 次），
   顺带把分布一并报出来（0~4 次的占比），比单一均值有信息量。
8. **不实测就别写成实测**——IL 读出的是**逻辑**，运行时表现（标 1 份还是整堆、实际掉落）可能还有引擎层语义；
   结论里要写明「取自 IL，运行未实测」。
