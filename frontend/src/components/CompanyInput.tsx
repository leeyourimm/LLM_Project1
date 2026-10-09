"use client";

import { useId } from "react";
import { useApp } from "./providers";

export function CompanyInput(props: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  required?: boolean;
  label?: string;
  className?: string;
}) {
  const { companies } = useApp();
  const id = useId();
  return (
    <div className={props.className}>
      {props.label ? (
        <label htmlFor={`${id}-input`} className="label">
          {props.label}
        </label>
      ) : null}
      <input
        id={`${id}-input`}
        className="input"
        list={`${id}-list`}
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        placeholder={props.placeholder ?? "회사 이름 또는 종목코드"}
        required={props.required}
        autoComplete="off"
        aria-label={props.label ? undefined : props.placeholder ?? "회사"}
      />
      <datalist id={`${id}-list`}>
        {companies.map((c) => (
          <option key={c.corp_code} value={`${c.corp_name} (${c.stock_code})`} />
        ))}
      </datalist>
    </div>
  );
}
