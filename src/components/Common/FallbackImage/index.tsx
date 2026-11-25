import { useState } from "react";

type FallbackImageProps = React.ImgHTMLAttributes<HTMLImageElement> & {
  src?: string;
  fallbackSrc?: string;
};

export const FallbackImage = ({ src, alt, className, fallbackSrc = "/app-icon.svg", ...props }: FallbackImageProps) => {
    const [error, setError] = useState(false);
    const handleError = () => {
        setError(true);
    };

    if (error) {
        return <img src={fallbackSrc} alt={alt} className={className} {...props} onError={handleError}  />;
    }

    return <img src={error ? fallbackSrc : src} alt={alt} className={className} {...props} onError={handleError} />;
}
