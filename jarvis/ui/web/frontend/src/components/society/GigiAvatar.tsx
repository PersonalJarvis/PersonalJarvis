import { useId } from "react";
import "./GigiAvatar.css";

/** Graphite/gold companion portrait, drawn and animated without a GPU context. */
export function GigiAvatar({ size = 56 }: { size?: number }) {
  const id = `gigi-avatar-${useId().replace(/:/g, "")}`;
  const paint = (name: string) => `url(#${id}-${name})`;
  return (
    <span className="gigi-avatar agent-symbol-character" data-agent-mascot="gigi" data-animated={size >= 48} aria-hidden="true" style={{ width: size, height: size }}>
      <svg viewBox="0 0 240 260" width={size} height={size} fill="none" focusable="false">
        <defs>
          {/* Character material colours intentionally stay consistent across themes. */}
          <radialGradient id={`${id}-shell`} cx="34%" cy="18%" r="85%">
            <stop stopColor="#41403e" /><stop offset=".3" stopColor="#222224" /><stop offset=".7" stopColor="#101012" /><stop offset="1" stopColor="#070709" />
          </radialGradient>
          <radialGradient id={`${id}-lens`} cx="38%" cy="12%" r="95%">
            <stop stopColor="#333335" /><stop offset=".32" stopColor="#171719" /><stop offset=".8" stopColor="#070708" /><stop offset="1" stopColor="#141312" />
          </radialGradient>
          <linearGradient id={`${id}-metal`} x1="0" y1="0" x2="1" y2=".45">
            <stop stopColor="#876333" /><stop offset=".19" stopColor="#ffe3a0" /><stop offset=".36" stopColor="#3c3428" /><stop offset=".7" stopColor="#1a1817" /><stop offset="1" stopColor="#bb9150" />
          </linearGradient>
          <linearGradient id={`${id}-gold`} x1="0" y1="0" x2=".8" y2="1">
            <stop stopColor="#fff2bd" /><stop offset=".36" stopColor="#ffd769" /><stop offset=".72" stopColor="#ffbd36" /><stop offset="1" stopColor="#c98319" />
          </linearGradient>
          <radialGradient id={`${id}-eye`}>
            <stop stopColor="#ffeaa2" /><stop offset=".68" stopColor="#ffda61" /><stop offset="1" stopColor="#ffb72c" />
          </radialGradient>
          <radialGradient id={`${id}-pool`}>
            <stop stopColor="#ffc247" stopOpacity=".65" /><stop offset=".45" stopColor="#e9a83a" stopOpacity=".2" /><stop offset="1" stopColor="#d99c30" stopOpacity="0" />
          </radialGradient>
          <linearGradient id={`${id}-edge`}>
            <stop stopColor="#fff1c4" /><stop offset=".35" stopColor="#c18f3e" /><stop offset=".62" stopColor="#79592c" /><stop offset="1" stopColor="#ffe3a0" />
          </linearGradient>
          <filter id={`${id}-glow`} x="-60%" y="-60%" width="220%" height="220%" colorInterpolationFilters="sRGB">
            <feGaussianBlur stdDeviation="3" />
          </filter>
        </defs>
        <ellipse className="gigi-avatar__shadow" cx="120" cy="242" rx="57" ry="9" fill={paint("pool")} />
        <g className="gigi-avatar__float"><g className="gigi-avatar__pose">
          <g className="gigi-avatar__arm--left">
            <path d="M47 131C36 139 24 159 24 174C24 187 34 188 41 174L54 145Z" fill={paint("metal")} stroke={paint("edge")} strokeWidth="1.5" />
            <path d="M44 139C33 151 29 165 30 176C34 179 41 162 48 148Z" fill={paint("gold")} />
          </g>
          <g className="gigi-avatar__arm--right">
            <path d="M193 131C204 139 216 159 216 174C216 187 206 188 199 174L186 145Z" fill={paint("metal")} stroke={paint("edge")} strokeWidth="1.5" />
            <path d="M196 139C207 151 211 165 210 176C206 179 199 162 192 148Z" fill={paint("gold")} />
          </g>
          <rect x="37" y="89" width="15" height="39" rx="7.5" fill={paint("metal")} stroke="#8d7042" />
          <rect x="188" y="89" width="15" height="39" rx="7.5" fill={paint("metal")} stroke="#8d7042" />
          <path d="M40 98V118M200 98V118" stroke={paint("gold")} strokeWidth="3" strokeLinecap="round" />
          <path d="M45 113V94C45 45 73 22 120 22S195 45 195 94V184Q195 194 187 202L171 218L147 200L120 224L93 200L69 218L53 202Q45 194 45 184Z" fill={paint("shell")} stroke={paint("metal")} strokeWidth="2.5" />
          <path d="M49 118V94C49 48 75 26 120 26S191 48 191 94V118Z" fill={paint("lens")} />
          <path d="M46 100V94C46 46 75 23 120 23S194 46 194 94V100" stroke="#ffcd61" strokeWidth="3" filter={paint("glow")} />
          <path d="M46 99V94C46 46 75 23 120 23S194 46 194 94V99" stroke={paint("gold")} strokeWidth="1.8" />
          <path d="M60 59C72 36 95 27 119 27C145 27 165 35 177 51" stroke="#fff4d6" strokeOpacity=".72" strokeWidth="1.1" strokeLinecap="round" />
          <path d="M51 124V181Q51 193 58 197M188 127V180Q188 191 184 196" stroke={paint("edge")} strokeOpacity=".28" strokeWidth="1.3" />
          <path d="M46 119H194" stroke="#060606" strokeWidth="3.5" />
          <path d="M46 119H194" stroke={paint("edge")} strokeWidth="1" />
          <path d="M53 202L69 218L93 200L120 224L147 200L171 218L187 202" stroke="#ffb930" strokeWidth="7" strokeLinejoin="round" filter={paint("glow")} />
          <path d="M53 202L69 218L93 200L120 224L147 200L171 218L187 202" stroke={paint("gold")} strokeWidth="3.5" strokeLinejoin="round" />
          <path d="M56 203L69 215L93 197L120 221L147 197L171 215L185 202" stroke="#ffefb4" strokeWidth="1.1" strokeLinejoin="round" />
          <g className="gigi-avatar__eyes-open">
            {[89, 151].map((x, index) => (
              <g key={x} className={`gigi-avatar__eye--${index ? "right" : "left"}`}>
                <ellipse cx={x} cy="93" rx="16" ry="22" fill="#ffbc38" opacity=".65" filter={paint("glow")} />
                <ellipse cx={x} cy="93" rx="13" ry="18" fill={paint("eye")} />
                <ellipse cx={x + 2} cy="98" rx="6" ry="9" fill="#090909" />
                <ellipse cx={x + 5} cy="88" rx="3" ry="3.5" fill="#fffdf2" />
              </g>
            ))}
          </g>
          <path className="gigi-avatar__eyes-happy" d="M78 97C78 78 100 78 100 97M140 97C140 78 162 78 162 97" stroke={paint("gold")} strokeWidth="4" strokeLinecap="round" />
          <path className="gigi-avatar__wink" d="M140 97C140 78 162 78 162 97" stroke={paint("gold")} strokeWidth="4" strokeLinecap="round" />
          <g className="gigi-avatar__mouth-open">
            <ellipse cx="120" cy="137" rx="6" ry="9" stroke="#ffbd39" strokeWidth="4" filter={paint("glow")} />
            <ellipse cx="120" cy="137" rx="5.5" ry="8" stroke={paint("gold")} strokeWidth="2.7" />
          </g>
          <path className="gigi-avatar__smile" d="M113 135Q120 146 127 135Q120 139 113 135Z" fill={paint("gold")} />
          <path className="gigi-avatar__sparks" d="M203 58L206 49M212 65L220 61M210 77L218 78" stroke={paint("gold")} strokeWidth="2.8" strokeLinecap="round" />
        </g></g>
      </svg>
    </span>
  );
}
