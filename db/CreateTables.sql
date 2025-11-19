--類別表
CREATE TABLE Categories(
	CategoryID INT IDENTITY(1,1) PRIMARY KEY,
	CategoryName NVARCHAR(50) NOT NULL UNIQUE
);

--產品表(FK :CategoryID referies Categories)
CREATE TABLE Products(
	ProductID INT IDENTITY(1,1) PRIMARY KEY,
	ProductName NVARCHAR(100) NOT NULL,
	Price DECIMAL(10) DEFAULT 0, --預設0當時價
	CategoryID INT NOT NULL,
	CONSTRAINT FK_Category FOREIGN KEY (CategoryID) REFERENCES Categories(CategoryID)
);

--標籤表(存 '味道')
CREATE TABLE Tags(
	TagID INT IDENTITY(1,1) PRIMARY KEY,
	Tagname NVARCHAR(20) NOT NULL UNIQUE
);
--產品與標籤的關聯表(多對多關係的中間表)
CREATE TABLE ProductTags(
	ProductID INT NOT NULL,
	TagID INT NOT NULL,
	PRIMARY KEY (ProductID, TagID), --複合主鍵
	CONSTRAINT FK_Product FOREIGN KEY (ProductID) REFERENCES Products(ProductID),
	CONSTRAINT FK_Tag FOREIGN KEY (TagID) REFERENCES Tags(TagID)
);